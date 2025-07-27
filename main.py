import logging
import json
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import yt_dlp
import asyncio
from pathlib import Path
import tempfile
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get configuration from environment variables
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_IDS = [int(id.strip()) for id in os.getenv('ADMIN_IDS', '').split(',') if id.strip()]
REQUIRED_CHANNEL = os.getenv('REQUIRED_CHANNEL', '')

# Configuration file
CONFIG_FILE = "bot_config.json"

# Validate required configuration
if not BOT_TOKEN or not ADMIN_IDS:
    raise ValueError("Missing required environment variables: BOT_TOKEN and ADMIN_IDS")

# Default configuration
DEFAULT_CONFIG = {
    "required_channel": REQUIRED_CHANNEL
}

# Load or create configuration
def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                # Ensure all keys are present
                for key, value in DEFAULT_CONFIG.items():
                    if key not in config:
                        config[key] = value
                return config
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            return DEFAULT_CONFIG
    else:
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG

def save_config(config):
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving config: {e}")

# User sessions to track download progress
user_sessions = {}

# Supported formats
FORMATS = {
    'best': 'Best Quality',
    'best[height<=720]': '720p',
    'best[height<=480]': '480p',
    'bestaudio/best': 'Audio Only',
    'mp3': 'MP3 Audio'
}

class ProgressHook:
    def __init__(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
        self.update = update
        self.context = context
        self.user_id = user_id
        self.last_update = 0
        self.message_id = None
        self.last_progress_text = ""

    async def __call__(self, d):
        if d['status'] == 'downloading':
            # Update progress every 2 seconds to avoid spamming
            import time
            current_time = time.time()
            if current_time - self.last_update < 2:
                return
            self.last_update = current_time
            
            # Get progress information
            percent = d.get('_percent_str', '0%')
            speed = d.get('_speed_str', 'N/A')
            eta = d.get('_eta_str', 'N/A')
            
            # Create progress bar
            try:
                percent_float = float(percent.replace('%', ''))
                filled_blocks = int(percent_float // 5)  # 20 blocks for 100%
                progress_bar = '█' * filled_blocks + '░' * (20 - filled_blocks)
                progress_text = f"📥 Downloading: [{progress_bar}] {percent}\n⚡ Speed: {speed}\n⏱ ETA: {eta}"
            except:
                progress_text = f"📥 Downloading...\n📊 Progress: {percent}\n⚡ Speed: {speed}\n⏱ ETA: {eta}"
            
            # Only update if the text has changed
            if progress_text != self.last_progress_text:
                self.last_progress_text = progress_text
                # Update message
                try:
                    if self.message_id is None:
                        # Send initial progress message
                        message = await self.context.bot.send_message(
                            chat_id=self.user_id,
                            text=progress_text
                        )
                        self.message_id = message.message_id
                    else:
                        # Edit existing message
                        await self.context.bot.edit_message_text(
                            chat_id=self.user_id,
                            message_id=self.message_id,
                            text=progress_text
                        )
                except Exception as e:
                    logger.warning(f"Could not update progress message: {e}")
                
        elif d['status'] == 'finished':
            # Update message to show processing
            try:
                if self.message_id:
                    await self.context.bot.edit_message_text(
                        chat_id=self.user_id,
                        message_id=self.message_id,
                        text="✅ Download complete! Processing file..."
                    )
            except Exception as e:
                logger.warning(f"Could not update progress message: {e}")

class YouTubeDownloaderBot:
    def __init__(self, token):
        self.token = token
        self.application = None
        
    def get_required_channel(self):
        """Get the current required channel from config file"""
        config = load_config()
        return config.get('required_channel', REQUIRED_CHANNEL)
        
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send a message when the command /start is issued."""
        user = update.effective_user
        required_channel = self.get_required_channel()
        
        # Create the welcome message with proper Markdown escaping
        welcome_message = (
            f"Hello {user.first_name}\! 👋\n\n"
            "I'm your YouTube Video Downloader bot\. 🎥\n\n"
            f"⚠️ *Important*\: You must join our channel {required_channel} to use this bot\!\n\n"
            "Send me a YouTube URL and I'll help you download it\!\n\n"
            "*Commands*\:\n"
            "/start \- Start the bot\n"
            "/help \- Get help\n"
            "/formats \- See available formats\n"
            "/check \- Verify channel membership\n"
        )
        
        # Add admin commands if user is admin
        if user.id in ADMIN_IDS:
            welcome_message += (
                "\n🔐 *Admin Commands*\:\n"
                "/setchannel \- Set required channel\n"
                "/getchannel \- Get current channel\n"
            )
        
        try:
            await update.message.reply_text(
                welcome_message, 
                parse_mode='MarkdownV2',
                disable_web_page_preview=True
            )
        except Exception as e:
            logger.error(f"Error sending welcome message: {e}")
            # Fallback to plain text if Markdown fails
            await update.message.reply_text(
                welcome_message.replace('*', '').replace('`', ''),
                disable_web_page_preview=True
            )


    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send a message when the command /help is issued."""
        help_text = (
            "📥 *How to use me:*\n\n"
            "1. Send me a YouTube video URL\n"
            "2. Choose your preferred format\n"
            "3. Watch the progress bar during download\n"
            "4. Receive your video/audio file\n\n"
            "💡 *Tips:*\n"
            "- For playlists, I'll download the first video\n"
            "- Large files may take longer to process\n"
            "- I support MP3 conversion for audio\n\n"
            "⚠️ *Note:*\n"
            "Downloading copyrighted content may violate terms of service.\n"
            "Use responsibly and respect content creators."
        )
        
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def formats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show available formats."""
        formats_text = "*Available Formats:*\n\n"
        for key, value in FORMATS.items():
            formats_text += f"• {value} (`{key}`)\n"
        await update.message.reply_text(formats_text, parse_mode='Markdown')

    async def check_membership(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Check if user is a member of the required channel."""
        user_id = update.effective_user.id
        required_channel = self.get_required_channel()
        
        try:
            member = await context.bot.get_chat_member(required_channel, user_id)
            if member.status in ['member', 'administrator', 'creator']:
                await update.message.reply_text(f"✅ You are a member of {required_channel}!")
                return True
            else:
                await update.message.reply_text(f"❌ You are not a member of {required_channel}.")
                return False
        except Exception as e:
            logger.error(f"Error checking membership: {e}")
            await update.message.reply_text(
                f"⚠️ Could not verify membership. Please make sure:\n"
                f"1. You've joined {required_channel}\n"
                f"2. The channel is public\n"
                f"3. You're not restricting bot access in privacy settings"
            )
            return False

    async def set_channel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin command to set the required channel."""
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return
        
        if not context.args:
            await update.message.reply_text("Usage: /setchannel @channelusername")
            return
        
        channel = context.args[0].strip()
        if not channel.startswith('@'):
            await update.message.reply_text("Channel username must start with @")
            return
        
        # Update configuration
        config = load_config()
        config['required_channel'] = channel
        save_config(config)
        
        await update.message.reply_text(f"✅ Required channel set to {channel}")

    async def get_channel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin command to get the current required channel."""
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ You don't have permission to use this command.")
            return
        
        required_channel = self.get_required_channel()
        await update.message.reply_text(f"Current required channel: {required_channel}")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming messages (URLs)."""
        text = update.message.text.strip()
        
        # Check if it's a valid YouTube URL
        if not ("youtube.com" in text or "youtu.be" in text):
            await update.message.reply_text("Please send a valid YouTube URL.")
            return
            
        user_id = update.effective_user.id
        user_sessions[user_id] = {'url': text}
        
        # Create format selection buttons
        keyboard = []
        for key, value in FORMATS.items():
            keyboard.append([InlineKeyboardButton(value, callback_data=f"format_{key}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Select download format:",
            reply_markup=reply_markup
        )

    async def format_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle format selection buttons."""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        format_key = query.data.split('_')[1]
        
        if user_id not in user_sessions:
            await query.edit_message_text("Session expired. Please send the URL again.")
            return
            
        url = user_sessions[user_id]['url']
        user_sessions[user_id]['format'] = format_key
        
        # Acknowledge format selection
        format_name = FORMATS.get(format_key, format_key)
        await query.edit_message_text(f"Selected: {format_name}\nStarting download...")
        
        # Start download in background
        asyncio.create_task(self.process_download(update, context, user_id, url, format_key))

    async def process_download(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_id, url, format_key):
        """Handle the download process with proper error handling."""
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                # Initialize progress tracking
                progress_hook = ProgressHook(update, context, user_id)
                
                # Configure download options
                ydl_opts = {
                    'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
                    'noplaylist': True,
                    'progress_hooks': [progress_hook],
                    'quiet': True,
                    'no_warnings': True,
                }
                
                # Handle format-specific options
                if format_key == 'mp3':
                    ydl_opts.update({
                        'format': 'bestaudio/best',
                        'postprocessors': [{
                            'key': 'FFmpegExtractAudio',
                            'preferredcodec': 'mp3',
                            'preferredquality': '192',
                        }],
                    })
                else:
                    ydl_opts['format'] = format_key
                
                # Download the video
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info_dict = ydl.extract_info(url, download=True)
                    filename = ydl.prepare_filename(info_dict)
                
                # Handle MP3 conversion if needed
                if format_key == 'mp3':
                    mp3_filename = filename.rsplit('.', 1)[0] + '.mp3'
                    if os.path.exists(mp3_filename):
                        filename = mp3_filename
                
                # Send the appropriate file type
                title = info_dict.get('title', 'video')
                if filename.endswith('.mp3'):
                    await context.bot.send_audio(
                        chat_id=user_id,
                        audio=open(filename, 'rb'),
                        title=title
                    )
                elif any(filename.endswith(ext) for ext in ['.mp4', '.webm', '.mkv']):
                    await context.bot.send_video(
                        chat_id=user_id,
                        video=open(filename, 'rb'),
                        caption=title
                    )
                else:
                    await context.bot.send_document(
                        chat_id=user_id,
                        document=open(filename, 'rb'),
                        caption=title
                    )
                
        except yt_dlp.DownloadError as e:
            error_msg = "Download failed. The video may be restricted or unavailable."
            logger.error(f"Download error: {str(e)}")
        except Exception as e:
            error_msg = f"An error occurred: {str(e)}"
            logger.error(f"Unexpected error: {str(e)}")
        else:
            # Clean up session on success
            if user_id in user_sessions:
                del user_sessions[user_id]
            return
        
        # Handle errors
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"❌ {error_msg}\nPlease try again with a different video."
            )
        except Exception as e:
            logger.error(f"Failed to send error message: {str(e)}")
        
        # Clean up session on error
        if user_id in user_sessions:
            del user_sessions[user_id]

    def run(self):
        """Start the bot."""
        # Create the Application
        self.application = Application.builder().token(self.token).build()

        # Register handlers
        handlers = [
            CommandHandler("start", self.start),
            CommandHandler("help", self.help_command),
            CommandHandler("formats", self.formats_command),
            CommandHandler("check", self.check_membership),
            CommandHandler("setchannel", self.set_channel_command),
            CommandHandler("getchannel", self.get_channel_command),
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message),
            CallbackQueryHandler(self.format_button, pattern='^format_')
        ]
        
        for handler in handlers:
            self.application.add_handler(handler)

        # Run the bot
        logger.info("Starting bot...")
        self.application.run_polling()

if __name__ == '__main__':
    bot = YouTubeDownloaderBot(BOT_TOKEN)
    bot.run()