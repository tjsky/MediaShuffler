# main.py
import os
import re
import logging
import yaml
import sqlite3
import asyncio
from pathlib import Path
from datetime import datetime
from telegram import Bot
from telegram.ext import Application, CommandHandler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from contextlib import contextmanager

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 加载配置
with open('config.yaml', encoding='utf-8') as f:
    full_config = yaml.safe_load(f)
    config = full_config['bot']

# 初始化数据库
DB_PATH = 'media.db'
conn_pool = sqlite3.connect(DB_PATH, check_same_thread=False)

@contextmanager
def get_db():
    try:
        cursor = conn_pool.cursor()
        yield cursor
    finally:
        conn_pool.commit()
        cursor.close()

with get_db() as c:
    c.execute('''CREATE TABLE IF NOT EXISTS media (
             id INTEGER PRIMARY KEY,
             path TEXT UNIQUE,
             created_at DATETIME,
             is_deleted BOOLEAN DEFAULT 0)''')
             
with get_db() as c:
    c.execute('''CREATE TABLE IF NOT EXISTS sent_media (
             media_id INTEGER,
             sent_at DATETIME)''')
             
with get_db() as c:
    c.execute('''CREATE TRIGGER IF NOT EXISTS keep_1000_records
             AFTER INSERT ON sent_media
             BEGIN
                 DELETE FROM sent_media 
                 WHERE rowid IN (
                     SELECT rowid FROM sent_media 
                     ORDER BY sent_at DESC 
                     LIMIT -1 OFFSET 1000
                 );
             END;''')
conn_pool.commit()

class ConfigValidator:
    @staticmethod
    def validate():
        try:
            datetime.strptime(config['daily_scan_time'], "%H:%M")
        except ValueError:
            logger.error("Invalid daily_scan_time format, should be HH:MM")
            exit(1)

        os_type = 'win' if os.name == 'nt' else 'linux'
        if not Path(config['scan_dir'][os_type]).exists():
            logger.error(f"Scan directory not exists: {config['scan_dir'][os_type]}")
            exit(1)

ConfigValidator.validate()

class MediaScanner:
    def __init__(self):
        os_type = 'win' if os.name == 'nt' else 'linux'
        self.scan_dir = Path(config['scan_dir'][os_type])
        self.blacklist = config['blacklist']
        self.valid_ext = {'.jpg', '.png', '.gif', '.webp', '.mp4'}
        self.sent_suffix = "_Sent"
        
    def cleanup_sent_files(self):     
        logger.info("Starting sent files cleanup...")
        renamed_count = 0
        
        with get_db() as c:
            c.execute('''SELECT m.path 
                    FROM media m
                    JOIN sent_media s ON m.id = s.media_id''')
            sent_files = [row[0] for row in c.fetchall()]
        
            for rel_path in sent_files:
                original_path = self.scan_dir / rel_path
                if not original_path.exists():
                    continue
            
                new_name = f"{original_path.stem}{self.sent_suffix}{original_path.suffix}"
                new_path = original_path.with_name(new_name)
            
                try:
                    original_path.rename(new_path)
                    renamed_count += 1
                except Exception as e:
                    logger.error(f"重命名失败：{original_path} → {new_path}，错误：{str(e)}")
        
            c.execute('''DELETE FROM sent_media''')
            logger.info(f"清理完成，重命名文件数：{renamed_count}，删除数据库记录数：{c.rowcount}")
        conn_pool.commit()

    def scan_files(self):
        self._perform_scan()
        self.cleanup_sent_files()

    def _perform_scan(self):
        logger.info("Starting media scan...")
        current_files = set()

        for file_path in self.scan_dir.rglob('*'):
            if self._is_valid_file(file_path):
                rel_path = str(file_path.relative_to(self.scan_dir))
                current_files.add(rel_path)

        self._update_database(current_files)
        logger.info(f"扫描完成，总文件数：{len(current_files)}")

    def _is_valid_file(self, path):
        return (path.suffix.lower() in self.valid_ext and
                not any(b in path.name for b in self.blacklist) and
                path.is_file())

    def _update_database(self, current_files):
        with get_db() as c:
            c.execute("SELECT path FROM media WHERE is_deleted=0")
            db_files = {row[0] for row in c.fetchall()}

            for new_file in current_files - db_files:
                c.execute("INSERT INTO media (path, created_at) VALUES (?, ?)",
                     (new_file, datetime.now()))

            for deleted in db_files - current_files:
                c.execute("UPDATE media SET is_deleted=1 WHERE path=?", (deleted,))
        conn_pool.commit()

class BotCommands:
    def __init__(self, app, scanner):
        self.app = app
        self.scanner = scanner
        app.add_handler(CommandHandler("start", self.start))
        app.add_handler(CommandHandler("set", self.send_media))
        app.add_handler(CommandHandler("redb", self.rescan))

    async def start(self, update, context):
        if not self._is_admin(update):
            return
        await update.message.reply_text("✅ Bot正在工作中……")

    async def send_media(self, update, context, manual=True):
        if manual and not self._is_admin(update):
            return

        media = self._get_random_media()
        await update.message.reply_text("✅ 已发送")
        if not media:
            logger.warning("没有找到媒体文件")
            return

        success = await self._send_to_channel(media)
        if success:
            self._update_sent_records(media[0])
            logger.info(f"Sent media: {media[1]}")

    async def rescan(self, update, context):
        if not self._is_admin(update):
            return
        self.scanner.scan_files()
        await update.message.reply_text("✅ 数据库已刷新")

    def _is_admin(self, update):
        return update.effective_user.id in config['admin_ids']

    def _get_random_media(self):
        with get_db() as c:
            c.execute('''SELECT m.id, m.path 
                    FROM media m
                    LEFT JOIN sent_media s ON m.id = s.media_id
                    WHERE m.is_deleted=0 AND s.media_id IS NULL
                    ORDER BY RANDOM() 
                    LIMIT 1''')
            return c.fetchone()

    async def _send_to_channel(self, media):
        full_path = self.scanner.scan_dir / media[1]
        try:
            with open(full_path, 'rb') as f:
                if media[1].endswith('.mp4'):
                    await self.app.bot.send_video(config['channel_id'], f)
                else:
                    await self.app.bot.send_photo(config['channel_id'], f)
            return True
        except Exception as e:
            logger.error(f"Failed to send media: {str(e)}")
            return False

    def _update_sent_records(self, media_id):
        with get_db() as c:
            c.execute("INSERT INTO sent_media VALUES (?, ?)", 
                    (media_id, datetime.now()))
        conn_pool.commit()

class SchedulerManager:
    def __init__(self, app, scanner, bot_commands):
        self.scheduler = BackgroundScheduler()
        self.app = app
        self.scanner = scanner
        self.bot_commands = bot_commands
        self.loop = None
        self._setup_jobs()

    def _setup_jobs(self):
        self._add_daily_maintenance_job()
        self._add_media_send_job()
        self._add_text_schedules()

    def _add_daily_maintenance_job(self):
        scan_time = config['daily_scan_time']
        hour, minute = map(int, scan_time.split(':'))
        trigger = CronTrigger(hour=hour, minute=minute, timezone='Asia/Shanghai')
        self.scheduler.add_job(
            self._execute_daily_tasks,
            trigger=trigger,
            name="daily_maintenance",
            max_instances=1
        )

    def _execute_daily_tasks(self):
        if self.loop and self.loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                self._async_daily_tasks(), 
                self.loop
            )
            try:
                future.result(timeout=300)
            except asyncio.TimeoutError:
                logger.error("每日任务执行超时")

    async def _async_daily_tasks(self):
        logger.info("开始每日文件扫描...")
        self.scanner.scan_files()
        logger.info("每日维护任务完成")

    def _add_media_send_job(self):
        interval = config['interval']
        self.scheduler.add_job(
            self._wrap_send_media,
            trigger='interval',
            minutes=interval,
            name="random_media_send",
            max_instances=2
        )

    def _wrap_send_media(self):
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self.bot_commands.send_media(None, None, manual=False),
                self.loop
            )

    def _add_text_schedules(self):
        for schedule in config['text_schedules']:
            self._add_single_text_job(schedule)

    def _add_single_text_job(self, schedule_config):
        try:
            trigger = self._parse_schedule(schedule_config['schedule'])
            content = schedule_config['content'].replace('\\n', '\n')
            self.scheduler.add_job(
                self._wrap_send_text,
                trigger=trigger,
                kwargs={'content': content},
                name=f"text_schedule_{schedule_config['name']}",
                max_instances=1
            )
        except Exception as e:
            logger.error(f"定时任务配置错误: {schedule_config['name']} - {str(e)}")

    def _parse_schedule(self, schedule_str):
        """支持多种定时格式的解析方法"""
        # 格式1: day HH:MM (每日任务)
        if schedule_str.startswith('day '):
            try:
                _, time_part = schedule_str.split(' ', 1)
                hour, minute = map(int, time_part.split(':'))
                return CronTrigger(
                    hour=hour,
                    minute=minute,
                    timezone='Asia/Shanghai'
                )
            except ValueError:
                raise ValueError(f"Invalid day format: {schedule_str}")

        # 格式2: week <星期数> HH:MM (每周任务)
        elif schedule_str.startswith('week '):
            parts = schedule_str.split()
            if len(parts) != 3:
                raise ValueError(f"Invalid week format: {schedule_str}")
            
            day_map = {
                '0': 'mon', '1': 'tue', '2': 'wed', '3': 'thu',
                '4': 'fri', '5': 'sat', '6': 'sun'
            }
            day = parts[1]
            hour, minute = map(int, parts[2].split(':'))
            
            return CronTrigger(
                day_of_week=day_map.get(day, day),
                hour=hour,
                minute=minute,
                timezone='Asia/Shanghai'
            )

        # 格式3: cron表达式
        elif schedule_str.startswith('cron '):
            cron_exp = schedule_str.split(' ', 1)[1]
            return CronTrigger.from_crontab(cron_exp, timezone='Asia/Shanghai')

        else:
            raise ValueError(f"Unsupported schedule format: {schedule_str}")

    def _wrap_send_text(self, content):
        if self.loop is not None:
            asyncio.run_coroutine_threadsafe(
                self._send_text_message(content),
                self.loop
            )

    async def _send_text_message(self, content):
        try:
            await self.app.bot.send_message(
                chat_id=config['channel_id'],
                text=content,
                parse_mode='MarkdownV2'
            )
            logger.info(f"定时文本已发送：{content[:50]}...")
        except Exception as e:
            logger.error(f"文本发送失败：{str(e)}")

    def start(self):
        """安全启动调度器"""
        try:
            self.loop = asyncio.get_event_loop()
        except RuntimeError as e:
            if "no current event loop" in str(e):
                self.loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.loop)
            else:
                raise
        
        self.scheduler.start()

    def shutdown(self):
        self.scheduler.shutdown(wait=False)

def main():
    application = Application.builder().token(config['token']).build()
    scanner = MediaScanner()
    scanner.scan_files()
    bot_commands = BotCommands(application, scanner)
    scheduler = SchedulerManager(application, scanner, bot_commands)
    
    try:
        scheduler.start()
        application.run_polling()
    finally:
        scheduler.shutdown()
        conn_pool.close()

if __name__ == '__main__':
    main()