import os
import pickle
import requests
import subprocess
import re
import sys
import io
import torch
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from ai_metadata_generator import extract_first_lines_from_srt, generate_title_description_tags

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

from pydub import AudioSegment

load_dotenv()


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
VOICE_ID = os.getenv("VOICE_ID")

uploads = {}

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or not update.message.text:
        return

    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    if "drive.google.com" in text:
        await update.message.reply_text("🔽 Скачиваю видео с Google Drive...")
        file_path = download_from_gdrive(text)
        if file_path:
            final_video_path = "downloads/video1.mp4"
            if os.path.exists(final_video_path):
                os.remove(final_video_path)
            os.rename(file_path, final_video_path)
            uploads[chat_id] = uploads.get(chat_id, {})
            uploads[chat_id]["video"] = final_video_path
            await update.message.reply_text("✅ Видео получено! Теперь отправь .srt субтитры.")
        else:
            await update.message.reply_text("❌ Не удалось скачать видео с Google Drive.")
        return

    await update.message.reply_text(
        "👋 Привет! Я озвучу и синхронизирую видео по субтитрам.\n"
        "📅 Отправь .mp4 файл или ссылку на Google Drive.\n"
        "📄 Затем .srt субтитры."
    )


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat:
        return

    chat_id = update.effective_chat.id
    file = update.message.document or update.message.video

    if not file or not file.file_name:
        await update.message.reply_text("⚠️ Только .mp4 и .srt файлы поддерживаются.")
        return

    os.makedirs("downloads", exist_ok=True)
    temp_path = f"downloads/{file.file_unique_id}_{file.file_name}"

    try:
        tg_file = await file.get_file()
        await tg_file.download_to_drive(temp_path)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")
        return

    uploads[chat_id] = uploads.get(chat_id, {})

    if file.file_name.endswith(".srt"):
        uploads[chat_id]["subs"] = temp_path
        await update.message.reply_text("✅ Субтитры получены!")
    elif file.file_name.endswith(".mp4"):
        final_video_path = "downloads/video1.mp4"
        if os.path.exists(final_video_path):
            os.remove(final_video_path)
        os.rename(temp_path, final_video_path)
        uploads[chat_id]["video"] = final_video_path
        await update.message.reply_text("✅ Видео получено!")
    else:
        await update.message.reply_text("⚠️ Только .mp4 и .srt файлы.")
        return

    if "video" in uploads[chat_id] and "subs" in uploads[chat_id]:
        await update.message.reply_text("🎮 Шеф Гия , мы Начинаем обработку...")

        # 1. извлекаем аудио из видео
        original_audio_path = "downloads/original_audio.mp3"
        extract_audio_from_video(uploads[chat_id]["video"], original_audio_path)

        # 2. синтезируем озвучку с учетом таймкодов, сохраняя паузы и музыку
        audio_path = synthesize_audio(uploads[chat_id]["subs"], original_audio_path)

        # 3. объединяем озвучку с видео
        dubbed_path = "downloads/video1_dubbed.mp4"
        merge_audio_with_video(uploads[chat_id]["video"], audio_path, dubbed_path)

        drive_link = upload_to_google_drive(dubbed_path, "dubbed_no_lipsync.mp4")
        await update.message.reply_text(f"🎤 Озвучка без липсинга: {drive_link}")

        # после озвучки, перед липсингом
        lines = extract_first_lines_from_srt(uploads[chat_id]["subs"])
        metadata = generate_title_description_tags(lines)
        await update.message.reply_text("📝 Описание и теги:\n" + metadata)

      #  try:
       #     synced = apply_lip_sync(uploads[chat_id]["video"], audio_path)
        #    drive_link_synced = upload_to_google_drive(synced, "dubbed_with_lipsync.mp4")
         #   await update.message.reply_text(f"🔗 Липсинг выполнен: {drive_link_synced}")
       # except Exception as e:
        #    await update.message.reply_text(f"⚠️ Липсинг не удался: {e}")
         #   await update.message.reply_text(f"🔄 Загружена версия без липсинга: {drive_link}")

        await update.message.reply_text("✅ Завершено!")
        clean_downloads_folder()
        uploads[chat_id] = {}


def extract_audio_from_video(video_path, output_audio_path):
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn",
        "-acodec", "mp3",
        output_audio_path
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def parse_srt(sub_path):
    segments = []
    with open(sub_path, 'r', encoding='utf-8') as f:
        content = f.read()

    blocks = content.strip().split('\n\n')
    for block in blocks:
        lines = block.split('\n')
        if len(lines) >= 3:
            time_line = lines[1]
            text_lines = lines[2:]
            text = ' '.join(text_lines).strip()

            m = re.match(r'(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})', time_line)
            if not m:
                continue

            start = (int(m.group(1))*3600 + int(m.group(2))*60 + int(m.group(3))) * 1000 + int(m.group(4))
            end = (int(m.group(5))*3600 + int(m.group(6))*60 + int(m.group(7))) * 1000 + int(m.group(8))

            segments.append({'start_ms': start, 'end_ms': end, 'text': text})
    return segments


def synthesize_audio(sub_path, original_audio_path):
    segments = parse_srt(sub_path)
    original_audio = AudioSegment.from_file(original_audio_path)

    result_audio = AudioSegment.empty()
    last_pos = 0

    def tts_elevenlabs(text):
        response = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}",
            headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
            json={
                "text": text,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {"stability": 0.7, "similarity_boost": 0.7}
            }
        )
        if not response.ok:
            raise Exception(f"❌ ElevenLabs: {response.status_code} — {response.text}")
        return AudioSegment.from_file(io.BytesIO(response.content), format="mp3")

    for seg in segments:
        start = seg["start_ms"]
        end = seg["end_ms"]
        duration = end - start

        print(f"🎙️ Озвучиваем: {seg['text'][:40]}...")


        # Добавим фон, если есть пауза до следующего сегмента
        if start > last_pos:
            result_audio += original_audio[last_pos:start]

        tts = tts_elevenlabs(seg["text"])

        # Синхронизация по длительности
        if len(tts) > duration:
            tts = tts[:duration]
        else:
            silence = AudioSegment.silent(duration=duration - len(tts))
            tts += silence

        result_audio += tts
        last_pos = end

    # Остаток оригинального звука
    if last_pos < len(original_audio):
        result_audio += original_audio[last_pos:]

    output_path = "downloads/audio_tts_synced.mp3"
    result_audio.export(output_path, format="mp3")
    return output_path




def merge_audio_with_video(video_path, audio_path, output_path):
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
        output_path
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def apply_lip_sync(video_path, audio_path):
    wav2lip = os.path.join(os.path.dirname(__file__), 'Wav2Lip', 'inference.py')
    checkpoint = os.path.join(os.path.dirname(__file__), 'Wav2Lip', 'wav2lip_gan.pth')
    output = video_path.replace(".mp4", "_synced.mp4")

    cmd = [sys.executable, wav2lip, "--checkpoint_path", checkpoint, "--face", video_path, "--audio", audio_path, "--outfile", output]
    print("⚙️ Запуск Wav2Lip:", " ".join(cmd))

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
        raise RuntimeError("❌ Wav2Lip завершился с ошибкой!")
    return output


def upload_to_google_drive(filepath: str, filename: str = None) -> str:
    SCOPES = ['https://www.googleapis.com/auth/drive.file']
    creds = None

    if os.path.exists('token_drive.pickle'):
        with open('token_drive.pickle', 'rb') as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('client_secret_drive.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token_drive.pickle', 'wb') as token:
            pickle.dump(creds, token)

    drive_service = build('drive', 'v3', credentials=creds)

    file_metadata = {'name': filename or os.path.basename(filepath)}
    media = MediaFileUpload(filepath, resumable=True)
    uploaded_file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()

    file_id = uploaded_file.get('id')
    drive_service.permissions().create(fileId=file_id, body={"role": "reader", "type": "anyone"}).execute()
    return f"https://drive.google.com/file/d/{file_id}/view"


def download_from_gdrive(link: str) -> str:
    os.makedirs("downloads", exist_ok=True)
    match = re.search(r"(?:id=|/d/)([a-zA-Z0-9_-]+)", link)
    if not match:
        return ""

    file_id = match.group(1)
    output = f"downloads/{file_id}.mp4"
    try:
        response = requests.get(f"https://drive.google.com/uc?export=download&id={file_id}", stream=True)
        if "text/html" in response.headers.get("Content-Type", ""):
            print("❌ HTML вместо файла — возможно, нужен confirm-token")
            return ""
        with open(output, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return output
    except Exception as e:
        print("Download error:", e)
        return ""

def clean_downloads_folder():
    allowed = {"audio_tts_synced.mp3", "video1_dubbed.mp4"}
    for filename in os.listdir("downloads"):
        if filename not in allowed:
            try:
                filepath = os.path.join("downloads", filename)
                os.remove(filepath)
            except Exception as e:
                print(f"⚠️ Не удалось удалить {filename}: {e}")


if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).read_timeout(600).write_timeout(600).build()
    app.add_handler(MessageHandler(filters.Document.ALL | filters.VIDEO, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("\u0411\u043e\u0442 \u0437\u0430\u043f\u0443\u0449\u0435\u043d...")
    app.run_polling()

