import asyncio
import os
import sys
from pathlib import Path

# --- KHẮC PHỤC LỖI IMPORT ---
# Chỉ định thư mục gốc của dự án (/opt/qlvb_ai) và thêm vào hệ thống tìm kiếm của Python
project_root = str(Path(__file__).parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
# ----------------------------

from dotenv import load_dotenv
from playwright.async_api import async_playwright

# Nạp hàm đăng nhập từ thư mục src/rpa/auth.py
from src.rpa.auth import login_with_retry

async def run_test():
    print("🚀 Bắt đầu khởi động Playwright...")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 720},
            ignore_https_errors=True
        )
        page = await context.new_page()

        load_dotenv()
        username = os.getenv("QLVB_USERNAME")
        password = os.getenv("QLVB_PASSWORD")

        if not username or not password:
            print("❌ Lỗi: Chưa cấu hình QLVB_USERNAME hoặc QLVB_PASSWORD trong file .env")
            await browser.close()
            return

        print(f"Bắt đầu quy trình kiểm thử với tài khoản: {username}")
        
        success = await login_with_retry(page, username, password)

        os.makedirs("logs", exist_ok=True)
        
        if success:
            print("✅ Báo cáo Test: Thành công! Hệ thống đã đăng nhập vào phiên làm việc.")
            await page.screenshot(path="logs/login_success_proof.png", full_page=True)
            print("📸 Đã lưu ảnh: logs/login_success_proof.png")
        else:
            print("❌ Báo cáo Test: Thất bại. Vui lòng kiểm tra lại log lỗi và định vị phần tử (Selector).")
            await page.screenshot(path="logs/login_failed_proof.png", full_page=True)
            print("📸 Đã lưu ảnh: logs/login_failed_proof.png để phục vụ debug.")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_test())
