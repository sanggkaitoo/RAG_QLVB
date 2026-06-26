import os
import sys
import json
import asyncio
import requests
import datetime
import asyncio
from pathlib import Path

project_root = str(Path(__file__).parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from dotenv import load_dotenv
from playwright.async_api import async_playwright
import google.generativeai as genai
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Tái sử dụng module đã có trong hệ thống
from src.rpa.auth import login_with_retry
from src.rag.ingest import extract_text

load_dotenv()

# Cấu hình API Keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ZALO_OA_TOKEN = os.getenv("ZALO_OA_TOKEN")
ZALO_USER_ID = os.getenv("ZALO_USER_ID") # ID Zalo của bạn nhận thông báo
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/home/qlvb/rag_qlvb/data/downloads")
PROCESSED_TASKS_FILE = os.path.join(DOWNLOAD_DIR, "processed_tasks.json")

# Cấu hình Google Tasks API
SCOPES = ['https://www.googleapis.com/auth/tasks']

def init_gemini():
    genai.configure(api_key=GEMINI_API_KEY)
    # Khai báo output trả về dạng JSON để dễ bóc tách
    return genai.GenerativeModel('gemini-1.5-pro', generation_config={"response_mime_type": "application/json"})

def analyze_task_with_gemini(model, leader_note, document_content):
    prompt = f"""
    Bạn là trợ lý AI đắc lực xử lý công việc hành chính và tham mưu tại Sở Khoa học và Công nghệ tỉnh Lào Cai.
    Dưới đây là một nhiệm vụ mới được giao trên hệ thống Quản lý văn bản:
    - Ý kiến chỉ đạo của Lãnh đạo: {leader_note}
    - Nội dung văn bản (trích xuất): {document_content[:5000]}...

    Hãy phân tích và trả về duy nhất một chuỗi JSON chuẩn chứa các thông tin sau:
    1. "summary": Tóm tắt ngắn gọn nhiệm vụ cần làm (dưới 50 chữ).
    2. "deadline": Hạn hoàn thành (định dạng YYYY-MM-DDTHH:MM:SS.000Z, nếu không rõ ghi "None").
    3. "action_steps": Một mảng (array) chứa 3 bước đề xuất để triển khai nhiệm vụ này. 
    
    Lưu ý phân loại: Nếu văn bản yêu cầu các nội dung liên quan đến "Đề án 06", hãy tự động gom nhóm đánh dấu chúng phục vụ cho việc báo cáo kết quả "Nghị quyết 57".
    """
    response = model.generate_content(prompt)
    try:
        return json.loads(response.text)
    except Exception as e:
        print("Lỗi bóc tách JSON từ AI:", e)
        return None

def create_google_task(task_summary, deadline, notes):
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
        creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    service = build('tasks', 'v1', credentials=creds)
    
    task_body = {
        'title': task_summary,
        'notes': notes,
    }
    if deadline and deadline != "None":
        task_body['due'] = deadline

    # Thêm vào list Tasks mặc định ('@default')
    result = service.tasks().insert(tasklist='@default', body=task_body).execute()
    return result.get('htmlLink')

def send_zalo_notification(task_info):
    url = "https://openapi.zalo.me/v3.0/oa/message/cs"
    headers = {
        "access_token": ZALO_OA_TOKEN,
        "Content-Type": "application/json"
    }
    
    steps_text = "\n".join([f"- {s}" for s in task_info.get('action_steps', [])])
    message = (
        f"🚨 BẠN CÓ NHIỆM VỤ MỚI 🚨\n\n"
        f"📌 Tóm tắt: {task_info.get('summary')}\n"
        f"⏳ Hạn chót: {task_info.get('deadline', 'Không rõ')}\n\n"
        f"💡 Đề xuất hướng xử lý:\n{steps_text}"
    )
    
    payload = {
        "recipient": {"user_id": ZALO_USER_ID},
        "message": {"text": message}
    }
    requests.post(url, headers=headers, json=payload)

async def process_task_detail(context, task_url, download_dir):
    """
    Hàm phụ trách mở tab mới, truy cập chi tiết nhiệm vụ và hứng toàn bộ file đính kèm.
    """
    page = await context.new_page()
    await page.goto(task_url)
    
    downloaded_files = []
    tabs_to_check = ["Văn bản đến", "Văn bản liên quan"]
    
    for tab_name in tabs_to_check:
        try:
            tab_locator = page.locator(f"//a[contains(., '{tab_name}')] | //div[contains(., '{tab_name}')] | //span[contains(., '{tab_name}')]")
            
            if await tab_locator.count() > 0:
                await tab_locator.first.click()
                await page.wait_for_timeout(1000) # Đợi DOM load danh sách file
                
                download_btn = page.locator('//*[@title="Tải xuống tất cả các file đính kèm"]')
                
                if await download_btn.count() > 0:
                    print(f"   📥 Đang kích hoạt tải xuống tại tab '{tab_name}'...")
                    
                    # 1. Khai báo danh sách chứa các luồng download
                    active_downloads = []
                    
                    # 2. Định nghĩa hàm hứng sự kiện
                    def handle_download(download):
                        active_downloads.append(download)

                    # 3. Gắn "tai nghe" (listener) vào trang
                    page.on("download", handle_download)
                    
                    # 4. Click nút để website bắn ra các file
                    await download_btn.first.click()
                    
                    # 5. Đợi 4 giây để hệ thống QLVB kịp đẩy ra toàn bộ các sự kiện tải xuống
                    await page.wait_for_timeout(4000)
                    
                    # 6. Lần lượt lưu từng file đã hứng được (await ở đây để đảm bảo file tải xong hoàn toàn mới đi tiếp)
                    for download in active_downloads:
                        file_path = os.path.join(download_dir, download.suggested_filename)
                        await download.save_as(file_path)
                        downloaded_files.append(file_path)
                        print(f"      └─ Đã lưu: {download.suggested_filename}")
                    
                    # 7. Tháo listener ra để không bị đụng độ nếu lặp sang tab "Văn bản liên quan"
                    page.remove_listener("download", handle_download)
                else:
                    print(f"   ℹ️ Không có nút tải xuống ở tab '{tab_name}'.")
        except Exception as e:
            print(f"   ⚠️ Lỗi khi quét tab '{tab_name}': {e}")
            
    await page.close()
    return downloaded_files

async def scrape_new_tasks():
    TARGET_TASK_URL = "https://egov1.laocai.gov.vn/work/unit/main/1?statustype=waiting"
    BASE_URL = "https://egov1.laocai.gov.vn" 
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()

        success = await login_with_retry(page, os.getenv("QLVB_USERNAME"), os.getenv("QLVB_PASSWORD"))
        if not success:
            await browser.close()
            return []

        await page.goto(TARGET_TASK_URL)
        await page.wait_for_selector("table tbody tr", timeout=15000)
        
        tasks = []
        rows = await page.locator("table tbody tr").all()
        
        print(f"🔍 Tìm thấy {len(rows)} dòng nhiệm vụ trên trang.")
        
        for idx, row in enumerate(rows):
            cells = row.locator("td")
            if await cells.count() < 10: continue
            
            try:
                # Trích xuất dữ liệu từ cột 6 (Tên nhiệm vụ) và cột 10 (Hạn xử lý)
                task_link_element = cells.nth(5).locator("a").first
                leader_note = await task_link_element.inner_text()
                deadline = await cells.nth(9).inner_text()
                
                href = await task_link_element.get_attribute("href")
                task_url = href if href.startswith("http") else BASE_URL + href
                
                print(f"\n⚙️ Đang xử lý nhiệm vụ [{idx + 1}]: {leader_note[:40]}...")
                
                # Mở tab ảo để vào chi tiết và tải toàn bộ file đính kèm
                downloaded_docs = await process_task_detail(context, task_url, DOWNLOAD_DIR)
                
                doc_content = ""
                
                # Trích xuất văn bản từ các file vừa tải về và dọn dẹp
                for doc_path in downloaded_docs:
                    try:
                        doc_content += extract_text(doc_path) + "\n"
                        os.remove(doc_path) # Dọn rác ngay lập tức
                    except Exception as e:
                        print(f"      ⚠️ Lỗi đọc nội dung file: {e}")

                tasks.append({
                    "leader_note": leader_note.strip(),
                    "deadline": deadline.strip(),
                    "document_content": doc_content
                })
                
            except Exception as e:
                print(f"⚠️ Lỗi khi xử lý dòng nhiệm vụ {idx + 1}: {e}")

        await browser.close()
        return tasks

async def main():
    print("Khởi động Bot Trợ lý Nhiệm vụ...")
    tasks = await scrape_new_tasks()
    
    if not tasks:
        print("Hôm nay chưa có nhiệm vụ mới.")
        return

    gemini_model = init_gemini()
    
    for task in tasks:
        print("Đang nhờ Gemini phân tích nhiệm vụ...")
        ai_analysis = analyze_task_with_gemini(gemini_model, task['leader_note'], task['document_content'])
        
        if ai_analysis:
            # 1. Đẩy vào Google Tasks
            steps_note = "Hướng giải quyết:\n" + "\n".join([f"- {s}" for s in ai_analysis.get('action_steps', [])])
            create_google_task(ai_analysis.get('summary'), ai_analysis.get('deadline'), steps_note)
            
            # 2. Bắn tin nhắn qua Zalo
            send_zalo_notification(ai_analysis)
            print("Đã xử lý và thông báo thành công một nhiệm vụ!")

if __name__ == "__main__":
    asyncio.run(main())