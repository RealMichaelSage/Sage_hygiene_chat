import asyncio
import logging
import os
import re
import datetime
import urllib.parse
import hashlib
import aiohttp
import base64
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message
from aiogram.enums import ContentType

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set. Check your .env file.")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL or SUPABASE_KEY is not set.")

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# Bitrix24 Sync API URL and Secure Key
CRM_BASE_URL = "http://bitrix24.aisage.ru"
SECURE_KEY = "CRM_SECURE_KEY_2026_SAGE"
ALLOWED_USERNAMES = ["michael_sage"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Supabase DB Helpers
# ---------------------------------------------------------------------------

async def get_project_by_thread(thread_id: int):
    if not thread_id:
        return None
    url = f"{SUPABASE_URL}/rest/v1/project_mappings?thread_id=eq.{thread_id}&status=eq.active"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    rows = await resp.json()
                    if rows:
                        row = rows[0]
                        return row.get("project_name"), row.get("project_path"), row.get("github_repo")
                else:
                    err_text = await resp.text()
                    logger.error("Supabase error (status=%s): %s", resp.status, err_text)
    except Exception as e:
        logger.error("Failed to query Supabase: %s", e)
    return None

async def register_project(thread_id: int, name: str, path: str, github_repo: str = None):
    url = f"{SUPABASE_URL}/rest/v1/project_mappings"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }
    payload = {
        "thread_id": thread_id,
        "project_name": name,
        "project_path": path,
        "github_repo": github_repo,
        "status": "active"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status not in (200, 201):
                    err_text = await resp.text()
                    logger.error("Failed to upsert project in Supabase (status=%s): %s", resp.status, err_text)
    except Exception as e:
        logger.error("Exception upserting project in Supabase: %s", e)

async def update_github_repo(thread_id: int, github_repo: str):
    url = f"{SUPABASE_URL}/rest/v1/project_mappings?thread_id=eq.{thread_id}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "github_repo": github_repo
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.patch(url, json=payload, headers=headers) as resp:
                if resp.status not in (200, 204):
                    err_text = await resp.text()
                    logger.error("Failed to update github repo in Supabase (status=%s): %s", resp.status, err_text)
    except Exception as e:
        logger.error("Exception updating github repo in Supabase: %s", e)

async def archive_project_in_db(thread_id: int):
    url = f"{SUPABASE_URL}/rest/v1/project_mappings?thread_id=eq.{thread_id}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "status": "archived"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.patch(url, json=payload, headers=headers) as resp:
                if resp.status not in (200, 204):
                    err_text = await resp.text()
                    logger.error("Failed to archive project in Supabase (status=%s): %s", resp.status, err_text)
    except Exception as e:
        logger.error("Exception archiving project in Supabase: %s", e)

async def get_active_projects_list():
    url = f"{SUPABASE_URL}/rest/v1/project_mappings?status=eq.active"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception as e:
        logger.error("Failed to get projects list from Supabase: %s", e)
    return []

# ---------------------------------------------------------------------------
# GitHub API Helpers
# ---------------------------------------------------------------------------

async def create_github_repo(slug: str) -> tuple:
    if not GITHUB_TOKEN:
        logger.warning("GITHUB_TOKEN is not set. Skipping auto repo creation.")
        return None, None
        
    url = "https://api.github.com/user/repos"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json"
    }
    payload = {
        "name": f"AiSage-Proekt-{slug}",
        "private": True
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status == 201:
                    data = await resp.json()
                    return data.get("ssh_url"), data.get("html_url")
                elif resp.status == 422:
                    logger.info("GitHub repo AiSage-Proekt-%s already exists, linking to it.", slug)
                    fallback_ssh = f"git@github.com:RealMichaelSage/AiSage-Proekt-{slug}.git"
                    fallback_web = f"https://github.com/RealMichaelSage/AiSage-Proekt-{slug}"
                    return fallback_ssh, fallback_web
                else:
                    err_text = await resp.text()
                    logger.error("Failed to create GitHub repo (status=%s): %s", resp.status, err_text)
    except Exception as e:
        logger.error("Exception creating GitHub repo: %s", e)
    return None, None

async def setup_local_git(project_path: str, repo_url: str) -> None:
    if not os.path.exists(os.path.join(project_path, ".git")):
        try:
            process = await asyncio.create_subprocess_exec(
                "git", "init",
                cwd=project_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await process.communicate()
        except Exception as e:
            logger.error("Git init failed in %s: %s", project_path, e)
            
    if repo_url:
        try:
            proc_rem = await asyncio.create_subprocess_exec(
                "git", "remote", "remove", "origin",
                cwd=project_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc_rem.communicate()
            
            proc_add = await asyncio.create_subprocess_exec(
                "git", "remote", "add", "origin", repo_url,
                cwd=project_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc_add.communicate()
        except Exception as e:
            logger.error("Failed to configure git remote in %s: %s", project_path, e)

def get_slug(name: str) -> str:
    name = name.lower()
    translit_map = {
        'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'yo','ж':'zh','з':'z','и':'i','й':'y','к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'kh','ц':'ts','ч':'ch','ш':'sh','щ':'sch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya'
    }
    for ru, en in translit_map.items():
        name = name.replace(ru, en)
    name = re.sub(r'[^a-z0-9_-]', '-', name)
    name = re.sub(r'-+', '-', name).strip('-')
    return name

# ---------------------------------------------------------------------------
# Dispatcher & Routers
# ---------------------------------------------------------------------------

dp = Dispatcher()
service_router = Router(name="service_messages")
task_router = Router(name="task_messages")
dp.include_router(service_router)
dp.include_router(task_router)

# ---------------------------------------------------------------------------
# Helper functions for Parsing and Subprocesses
# ---------------------------------------------------------------------------

def parse_task_list(text: str):
    """
    Parses date, project names, and tasks from the text.
    """
    date_str = None
    text_lower = text.lower()
    
    if "завтра" in text_lower:
        date_str = (datetime.date.today() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    elif "сегодня" in text_lower:
        date_str = datetime.date.today().strftime("%Y-%m-%d")
    else:
        match_date = re.search(r'(?:на|от)?\s*(\d+)\s+([а-яёА-ЯЁ]+)(?:\s+(\d{4}))?', text)
        if match_date:
            day = int(match_date.group(1))
            month_name = match_date.group(2).lower()
            year_str = match_date.group(3)
            year = int(year_str) if year_str else datetime.date.today().year
            
            months = {
                'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4,
                'мая': 5, 'июня': 6, 'июля': 7, 'августа': 8,
                'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12
            }
            if month_name in months:
                month = months[month_name]
                date_str = f"{year:04d}-{month:02d}-{day:02d}"
                
    if not date_str:
        date_str = datetime.date.today().strftime("%Y-%m-%d")

    projects = {}
    current_project = None
    
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
            
        proj_match = re.match(r'^(?:Проект|проект)\s+([^:\n]+):?$', line)
        if proj_match:
            current_project = proj_match.group(1).strip()
            projects[current_project] = []
            continue
            
        task_match = re.match(r'^\s*(?:\d+[\.\)]|[-•*])\s*(.*)$', line)
        if task_match and current_project:
            task_text = task_match.group(1).strip()
            if task_text:
                projects[current_project].append(task_text)
                
    return date_str, projects

async def run_agy_prompt(project_path: str, prompt: str) -> str:
    env = os.environ.copy()
    env["HTTP_PROXY"] = "http://127.0.0.1:8118"
    env["HTTPS_PROXY"] = "http://127.0.0.1:8118"
    env["http_proxy"] = "http://127.0.0.1:8118"
    env["https_proxy"] = "http://127.0.0.1:8118"
    env["PATH"] = "/root/.local/bin:" + env.get("PATH", "")
    
    logger.info("Executing agy command in %s with prompt: %s", project_path, prompt)
    
    try:
        process = await asyncio.create_subprocess_exec(
            "/root/.local/bin/agy", "--print", prompt,
            cwd=project_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
        stdout, stderr = await process.communicate()
        
        stdout_str = stdout.decode("utf-8", errors="replace").strip()
        stderr_str = stderr.decode("utf-8", errors="replace").strip()
        
        logger.info("agy finished with code %s", process.returncode)
        
        if process.returncode == 0:
            return stdout_str
        else:
            return f"❌ Ошибка выполнения Antigravity (код {process.returncode}):\n\n{stdout_str}\n\n{stderr_str}"
    except Exception as e:
        logger.error("Exception during agy exec: %s", e)
        return f"❌ Исключение при выполнении команды: {e}"

async def sync_tasks_to_crm(message: Message, date_str: str, projects: dict) -> None:
    url = f"{CRM_BASE_URL}/sync_tasks_meetings.php"
    results_report = []
    
    async with aiohttp.ClientSession() as session:
        for project_name, tasks in projects.items():
            if not tasks:
                continue
                
            project_report = [f"\n📁 **Проект {project_name}:**"]
            
            for idx, task_text in enumerate(tasks, 1):
                full_title = f"{project_name}: {task_text}"
                h = hashlib.md5(f"{project_name}:{task_text}".encode('utf-8')).hexdigest()[:8]
                xml_id = f"task_{date_str}_{h}"
                
                payload = {
                    "key": SECURE_KEY,
                    "action": "sync_task",
                    "title": full_title,
                    "due_date": date_str,
                    "xml_id": xml_id,
                    "source": "TG Bot"
                }
                
                try:
                    async with session.post(url, data=payload, timeout=20) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get("status") == "success":
                                action_type = data.get("action")
                                sync_status = data.get("sync_status")
                                
                                if action_type == "sync_crm_activity":
                                    deal_id = data.get("deal_id")
                                    status_icon = "💼"
                                    status_text = "создано в CRM" if sync_status == "created" else "уже записано в CRM"
                                    project_report.append(f"{idx}. {status_icon} {task_text} — **{status_text}** (Сделка ID: {deal_id})")
                                else:
                                    task_id = data.get("task_id")
                                    status_icon = "✅"
                                    status_text = "создано в Задачнике" if sync_status == "created" else "обновлено/уже есть"
                                    task_url = f"https://bitrix24.aisage.ru/company/personal/user/1/tasks/task/view/{task_id}/"
                                    project_report.append(f"{idx}. {status_icon} [{task_text}]({task_url}) — **{status_text}**")
                            else:
                                project_report.append(f"{idx}. ❌ {task_text} — **Ошибка**: {data.get('message')}")
                        else:
                            project_report.append(f"{idx}. ❌ {task_text} — **Ошибка сервера** ({resp.status})")
                except Exception as exc:
                    logger.error("Sync request failed for task '%s': %s", full_title, exc)
                    project_report.append(f"{idx}. ❌ {task_text} — **Ошибка сети**: {exc}")
                    
            results_report.append("\n".join(project_report))
            
    report_message = f"📊 **Отчет о синхронизации задач Михаил Пузырёв на {date_str}:**\n" + "\n".join(results_report)
    
    if len(report_message) > 4000:
        for chunk in [report_message[i:i+4000] for i in range(0, len(report_message), 4000)]:
            await message.answer(chunk, parse_mode="Markdown", disable_web_page_preview=True)
    else:
        await message.answer(report_message, parse_mode="Markdown", disable_web_page_preview=True)

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

@service_router.message(F.content_type.in_({
    ContentType.NEW_CHAT_MEMBERS,
    ContentType.LEFT_CHAT_MEMBER,
    ContentType.NEW_CHAT_TITLE,
    ContentType.NEW_CHAT_PHOTO,
    ContentType.DELETE_CHAT_PHOTO,
    ContentType.PINNED_MESSAGE,
    ContentType.VIDEO_CHAT_STARTED,
    ContentType.VIDEO_CHAT_ENDED,
    ContentType.VIDEO_CHAT_PARTICIPANTS_INVITED,
}))
async def delete_service_message(message: Message) -> None:
    """Delete join / leave service messages to keep the chat clean."""
    try:
        await message.delete()
        logger.info(
            "Deleted service message (type=%s) in chat %s",
            message.content_type,
            message.chat.id,
        )
    except Exception as exc:
        logger.error("Failed to delete service message: %s", exc)

@service_router.message(F.forum_topic_created)
async def handle_topic_created(message: Message) -> None:
    topic_name = message.forum_topic_created.name
    thread_id = message.message_thread_id
    logger.info("New topic created: %s (Thread ID: %s)", topic_name, thread_id)
    
    if topic_name.lower().startswith("проект:"):
        project_name = topic_name[7:].strip()
        slug = get_slug(project_name)
        project_path = f"/root/workspace/AiSage-Проект-{slug}"
        
        os.makedirs(project_path, exist_ok=True)
        os.makedirs(os.path.join(project_path, "docs"), exist_ok=True)
        
        # 1. Create GitHub Repo
        ssh_url, web_url = await create_github_repo(slug)
        
        # 2. Setup local git
        await setup_local_git(project_path, ssh_url)
        
        # 3. Register in Supabase
        await register_project(thread_id, project_name, project_path, ssh_url)
        
        github_note = f"\n🐙 **GitHub:** [Создан репозиторий]({web_url})" if web_url else "\n⚠️ Не удалось автоматически создать репозиторий на GitHub."
        await message.reply(f"🤖 **Google Antigravity: Проект зарегистрирован!**\n\n"
                            f"📁 Создана рабочая папка проекта: `{project_path}`\n"
                            f"⚙️ Git-репозиторий инициализирован.{github_note}",
                            parse_mode="Markdown",
                            disable_web_page_preview=True)

@task_router.message(F.text == "/status")
async def status_command(message: Message) -> None:
    if not message.from_user or not message.from_user.username or message.from_user.username.lower() not in ALLOWED_USERNAMES:
        return
        
    import shutil
    total, used, free = shutil.disk_usage("/")
    free_gb = free / (1024**3)
    used_gb = used / (1024**3)
    total_gb = total / (1024**3)
    
    rows = await get_active_projects_list()
    
    project_list = []
    for row in rows:
        th_id = row.get("thread_id")
        name = row.get("project_name")
        path = row.get("project_path")
        repo = row.get("github_repo")
        repo_str = f" ([GitHub]({repo}))" if repo else ""
        project_list.append(f"• **{name}** (Тема ID: `{th_id}`){repo_str}\n  `{path}`")
        
    proj_str = "\n".join(project_list) if project_list else "Нет активных проектов."
    
    status_text = (
        f"📊 **Статус системы Antigravity VPS:**\n\n"
        f"💾 **Диск:** Свободно {free_gb:.1f} GB из {total_gb:.1f} GB (Использовано {used_gb:.1f} GB)\n\n"
        f"📁 **Активные проекты:**\n{proj_str}"
    )
    
    await message.reply(status_text, parse_mode="Markdown", disable_web_page_preview=True)

@task_router.message(F.text == "/archive")
async def archive_command(message: Message) -> None:
    if not message.from_user or not message.from_user.username or message.from_user.username.lower() not in ALLOWED_USERNAMES:
        return
        
    thread_id = message.message_thread_id
    project = await get_project_by_thread(thread_id)
    if not project:
        await message.reply("⚠️ Эта тема не привязана к активному проекту.")
        return
        
    proj_name, proj_path, _ = project
    status_msg = await message.reply(f"📦 Начинаю архивацию проекта **{proj_name}**...")
    
    try:
        git_pushed = False
        if os.path.exists(os.path.join(proj_path, ".git")):
            proc_check = await asyncio.create_subprocess_exec(
                "git", "remote",
                cwd=proj_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc_check.communicate()
            if b"origin" in stdout:
                await status_msg.edit_text(f"📦 Архивация **{proj_name}**: Делаю git push...")
                proc_push = await asyncio.create_subprocess_exec(
                    "git", "push", "-u", "origin", "main",
                    cwd=proj_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await proc_push.communicate()
                git_pushed = True
                
        import shutil
        archive_dir = "/root/archived_projects"
        os.makedirs(archive_dir, exist_ok=True)
        zip_path = os.path.join(archive_dir, f"{get_slug(proj_name)}_{datetime.date.today().strftime('%Y%m%d')}")
        
        await status_msg.edit_text(f"📦 Архивация **{proj_name}**: Создаю ZIP-архив...")
        shutil.make_archive(zip_path, 'zip', proj_path)
        
        await status_msg.edit_text(f"📦 Архивация **{proj_name}**: Удаляю рабочую папку с VPS...")
        shutil.rmtree(proj_path)
        
        await archive_project_in_db(thread_id)
        
        git_note = " (изменения запушены в GitHub)" if git_pushed else ""
        await status_msg.edit_text(f"✅ **Проект {proj_name} успешно архивирован!**\n\n"
                                    f"📁 Архив сохранен: `{zip_path}.zip`{git_note}.\n"
                                    f"Рабочая директория очищена.")
    except Exception as e:
        logger.error("Archive failed: %s", e)
        await status_msg.edit_text(f"❌ Ошибка архивации проекта: {e}")

@task_router.message(F.text.startswith("/link_project"))
async def link_project_command(message: Message) -> None:
    if not message.from_user or not message.from_user.username or message.from_user.username.lower() not in ALLOWED_USERNAMES:
        return
        
    thread_id = message.message_thread_id
    if not thread_id:
        await message.reply("⚠️ Эту команду можно вызывать только внутри тем (топиков) группы.")
        return
        
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("⚠️ Укажите имя проекта или slug. Пример:\n`/link_project vector` или `/link_project Проект Вектор`")
        return
        
    project_input = parts[1].strip()
    slug = get_slug(project_input)
    project_path = f"/root/workspace/AiSage-Проект-{slug}"
    
    os.makedirs(project_path, exist_ok=True)
    os.makedirs(os.path.join(project_path, "docs"), exist_ok=True)
    
    # 1. Create GitHub Repo
    ssh_url, web_url = await create_github_repo(slug)
    
    # 2. Setup local git
    await setup_local_git(project_path, ssh_url)
    
    # 3. Register in Supabase
    await register_project(thread_id, project_input, project_path, ssh_url)
    
    github_note = f"\n🐙 **GitHub:** [Привязан репозиторий]({web_url})" if web_url else "\n⚠️ Не удалось автоматически создать/привязать репозиторий на GitHub."
    await message.reply(f"🔗 **Тема успешно привязана к проекту!**\n\n"
                        f"📁 Папка проекта: `{project_path}`\n"
                        f"🤖 Antigravity готов к работе в этой теме.{github_note}",
                        parse_mode="Markdown",
                        disable_web_page_preview=True)

@task_router.message(F.text.startswith("/link_github"))
async def link_github_command(message: Message) -> None:
    if not message.from_user or not message.from_user.username or message.from_user.username.lower() not in ALLOWED_USERNAMES:
        return
        
    thread_id = message.message_thread_id
    project = await get_project_by_thread(thread_id)
    if not project:
        await message.reply("⚠️ Эта тема не привязана к проекту. Сначала вызовите `/link_project [имя]`.")
        return
        
    proj_name, proj_path, _ = project
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("⚠️ Укажите URL-адрес GitHub репозитория. Пример:\n`/link_github https://github.com/username/repo.git`")
        return
        
    repo_url = parts[1].strip()
    await update_github_repo(thread_id, repo_url)
    
    try:
        proc_rem = await asyncio.create_subprocess_exec(
            "git", "remote", "remove", "origin",
            cwd=proj_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc_rem.communicate()
        
        proc_add = await asyncio.create_subprocess_exec(
            "git", "remote", "add", "origin", repo_url,
            cwd=proj_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc_add.communicate()
        
        await message.reply(f"🔗 **Репозиторий GitHub привязан к проекту {proj_name}!**\n\nGit remote настроен на: `{repo_url}`")
    except Exception as e:
        logger.error("Failed to set git remote: %s", e)
        await message.reply(f"⚠️ Репозиторий сохранен в БД, но не удалось настроить git remote локально: {e}")

@task_router.message(F.content_type == ContentType.VOICE)
async def process_voice_task(message: Message) -> None:
    if not message.from_user or not message.from_user.username or message.from_user.username.lower() not in ALLOWED_USERNAMES:
        return

    logger.info("Processing voice message from Михаил Пузырёв...")
    
    temp_filename = f"temp_{message.voice.file_id}.ogg"
    try:
        voice = message.voice
        file_info = await message.bot.get_file(voice.file_id)
        file_path = file_info.file_path
        await message.bot.download_file(file_path, temp_filename)
        
        with open(temp_filename, "rb") as f:
            audio_bytes = f.read()
        base64_audio = base64.b64encode(audio_bytes).decode("utf-8")
        
    except Exception as e:
        logger.error("Failed to download or read voice file: %s", e)
        await message.reply(f"❌ Ошибка при скачивании голосового сообщения: {e}")
        return
    finally:
        if os.path.exists(temp_filename):
            try:
                os.remove(temp_filename)
            except Exception as ex:
                logger.error("Failed to delete temp file %s: %s", temp_filename, ex)

    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GLOBAL_GEMINI_API_KEY")
    if not gemini_key:
        await message.reply("❌ Ошибка: В настройках не найден API ключ Gemini.")
        return
        
    status_msg = await message.reply("⏳ Распознаю голосовое сообщение...")
    
    transcription = ""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}"
            payload = {
                "contents": [{
                    "parts": [
                        {"inlineData": {"mimeType": "audio/ogg", "data": base64_audio}},
                        {"text": "Сделай точную транскрибацию этой голосовой заметки на русском языке. Пиши только текст расшифровки без лишних слов."}
                    ]
                }]
            }
            async with session.post(url, json=payload, timeout=60) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    try:
                        transcription = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                    except KeyError:
                        logger.error("Invalid Gemini API response structure: %s", data)
                        await status_msg.edit_text("❌ Не удалось разобрать ответ от Gemini API.")
                        return
                else:
                    err_text = await resp.text()
                    logger.error("Gemini API error (status=%s): %s", resp.status, err_text)
                    await status_msg.edit_text(f"❌ Ошибка от Gemini API (код {resp.status})")
                    return
    except Exception as e:
        logger.error("Failed to contact Gemini API: %s", e)
        await status_msg.edit_text(f"❌ Сетевая ошибка при отправке в Gemini: {e}")
        return

    if not transcription:
        await status_msg.edit_text("⚠️ Голосовое сообщение пустое или не распознано.")
        return
        
    await status_msg.edit_text(f"📝 **Распознанный текст:**\n\n{transcription}")
    
    date_str, projects = parse_task_list(transcription)
    if projects:
        await sync_tasks_to_crm(message, date_str, projects)
    else:
        thread_id = message.message_thread_id
        project = await get_project_by_thread(thread_id)
        if project:
            proj_name, proj_path, _ = project
            progress_msg = await message.reply(f"⏳ Выполняю запрос в Antigravity для проекта **{proj_name}**...")
            result = await run_agy_prompt(proj_path, transcription)
            if len(result) > 4000:
                for chunk in [result[i:i+4000] for i in range(0, len(result), 4000)]:
                    await message.reply(chunk)
            else:
                await message.reply(result)
            try:
                await progress_msg.delete()
            except Exception:
                pass
        else:
            await message.reply("💡 Голосовое сообщение распознано, но эта тема не привязана ни к одному проекту. Создайте тему с префиксом `Проект: [Название]` или введите `/link_project [slug]` для привязки.")

@task_router.message(F.content_type == ContentType.DOCUMENT)
async def process_document(message: Message) -> None:
    if not message.from_user or not message.from_user.username or message.from_user.username.lower() not in ALLOWED_USERNAMES:
        return
        
    thread_id = message.message_thread_id
    project = await get_project_by_thread(thread_id)
    if not project:
        return
        
    proj_name, proj_path, _ = project
    doc = message.document
    filename = doc.file_name or f"file_{doc.file_id}"
    
    docs_dir = os.path.join(proj_path, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    dest_path = os.path.join(docs_dir, filename)
    
    status_msg = await message.reply(f"⏳ Скачиваю файл `{filename}` в папку `docs/` проекта...")
    
    try:
        file_info = await message.bot.get_file(doc.file_id)
        await message.bot.download_file(file_info.file_path, dest_path)
        await status_msg.edit_text(f"📥 Файл `{filename}` успешно сохранен в `docs/` проекта **{proj_name}**.")
    except Exception as e:
        logger.error("Failed to download document: %s", e)
        await status_msg.edit_text(f"❌ Ошибка при скачивании файла: {e}")

@task_router.message(F.content_type == ContentType.PHOTO)
async def process_photo(message: Message) -> None:
    if not message.from_user or not message.from_user.username or message.from_user.username.lower() not in ALLOWED_USERNAMES:
        return
        
    thread_id = message.message_thread_id
    project = await get_project_by_thread(thread_id)
    if not project:
        return
        
    proj_name, proj_path, _ = project
    photo = message.photo[-1]
    filename = f"photo_{photo.file_id}.jpg"
    
    docs_dir = os.path.join(proj_path, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    dest_path = os.path.join(docs_dir, filename)
    
    status_msg = await message.reply("⏳ Скачиваю изображение в папку `docs/`...")
    
    try:
        file_info = await message.bot.get_file(photo.file_id)
        await message.bot.download_file(file_info.file_path, dest_path)
        await status_msg.edit_text(f"📥 Изображение сохранено в `docs/` проекта **{proj_name}** как `{filename}`.")
    except Exception as e:
        logger.error("Failed to download photo: %s", e)
        await status_msg.edit_text(f"❌ Ошибка при скачивании изображения: {e}")

@task_router.message(F.text)
async def process_text_message(message: Message) -> None:
    if not message.from_user or not message.from_user.username or message.from_user.username.lower() not in ALLOWED_USERNAMES:
        return

    if message.text.startswith("/"):
        return
        
    date_str, projects = parse_task_list(message.text)
    if projects:
        await sync_tasks_to_crm(message, date_str, projects)
        return
        
    thread_id = message.message_thread_id
    project = await get_project_by_thread(thread_id)
    if not project:
        return
        
    proj_name, proj_path, _ = project
    progress_msg = await message.reply(f"⏳ Выполняю запрос в Antigravity для проекта **{proj_name}**...")
    
    result = await run_agy_prompt(proj_path, message.text)
    
    if len(result) > 4000:
        for chunk in [result[i:i+4000] for i in range(0, len(result), 4000)]:
            await message.reply(chunk)
    else:
        await message.reply(result)
        
    try:
        await progress_msg.delete()
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def start_web_server() -> None:
    from aiohttp import web
    
    async def handle(request):
        return web.Response(text="OK")

    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080)))
    await site.start()
    logger.info(f"Web server started on port {os.getenv('PORT', 8080)}")

async def main() -> None:
    bot = Bot(token=BOT_TOKEN)
    logger.info("Bot is starting…")
    
    await start_web_server()
    
    # Enable polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
