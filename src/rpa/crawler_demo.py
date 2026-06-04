import os
import json
import re
import asyncio
from dotenv import load_dotenv
from playwright.async_api import async_playwright
# Gọi hàm đăng nhập bán tự động (có dừng lại chờ nhập Captcha) của bạn
from src.rpa.auth import login_with_retry

load_dotenv()

# Nơi lưu file tải về
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/opt/qlvb_ai/data/downloads")

def sanitize_filename(name: str) -> str:
    """Xóa các ký tự cấm trong tên file của hệ điều hành."""
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()

async def download_demo_documents():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    
    # URL đích trang Văn bản đi
    TARGET_URL = "https://egov1.laocai.gov.vn/document/xem-di-index?statustype=published&type=vanbandi"
    TARGET_DOC_COUNT = 10 # Số lượng văn bản muốn tải để test

    async with async_playwright() as p:
        # Để headless=False để bạn xem bot chạy trực tiếp trên màn hình
        browser = await p.chromium.launch(headless=False) 
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()

        username = os.getenv("QLVB_USERNAME")
        password = os.getenv("QLVB_PASSWORD")

        print("🔄 Bắt đầu luồng đăng nhập (Hãy chú ý Terminal để nhập Captcha)...")
        success = await login_with_retry(page, username, password)
        if not success:
            print("❌ Dừng quá trình tải do không đăng nhập được.")
            await browser.close()
            return

        print(f"\n🌐 Đã vào hệ thống! Đang chuyển hướng tới: {TARGET_URL}")
        await page.goto(TARGET_URL)
        
        # Chờ bảng dữ liệu xuất hiện
        print("⏳ Đang đợi bảng dữ liệu tải lên...")
        await page.wait_for_selector("table tbody tr", timeout=15000)
        
        # Nghỉ thêm 2 giây để các nút tải file (thẻ a) render hoàn toàn
        await page.wait_for_timeout(2000)

        print(f"\n🚀 Bắt đầu quét và tải {TARGET_DOC_COUNT} văn bản đầu tiên...\n")
        
        # Lấy toàn bộ các hàng trong bảng
        rows = await page.locator("table tbody tr").all()
        
        downloaded_count = 0

        for row in rows:
            # Dừng lại nếu đã tải đủ số lượng yêu cầu
            if downloaded_count >= TARGET_DOC_COUNT:
                print(f"\n🎉 Đã tải đủ {TARGET_DOC_COUNT} văn bản Demo!")
                break

            cells = row.locator("td")
            
            # Bỏ qua nếu hàng không đủ cột (hàng rỗng hoặc lỗi)
            if await cells.count() < 6:
                continue
            
            # Trích xuất dữ liệu văn bản (Cột 2, 4, 5 tương ứng index 1, 3, 4)
            trich_yeu = await cells.nth(1).inner_text()
            so_ky_hieu = await cells.nth(3).inner_text()
            ngay_ban_hanh = await cells.nth(4).inner_text()
            
            trich_yeu = trich_yeu.strip()
            so_ky_hieu = so_ky_hieu.strip()
            ngay_ban_hanh = ngay_ban_hanh.strip()
            
            # Bỏ qua nếu không có số ký hiệu
            if not so_ky_hieu:
                continue

            # Lấy tất cả các thẻ <a> (nút tải file) ở cột cuối cùng
            file_links = await cells.last.locator("a").all()
            
            # Nếu văn bản này không có đính kèm file nào thì bỏ qua
            if not file_links:
                continue

            downloaded_count += 1
            print(f"[{downloaded_count}/{TARGET_DOC_COUNT}] Đang tải: {so_ky_hieu} - {trich_yeu[:50]}...")

            # Duyệt qua từng file đính kèm của văn bản đó
            for link_idx, link in enumerate(file_links):
                try:
                    # Chờ bắt sự kiện tải file
                    async with page.expect_download(timeout=15000) as download_info:
                        await link.click()
                    
                    download = await download_info.value
                    original_name = download.suggested_filename
                    
                    # Ghép Số ký hiệu vào tên file để chống trùng lặp (Ví dụ: 1255_SKHCN-QLKH_Bao_cao.pdf)
                    safe_so_ky_hieu = sanitize_filename(so_ky_hieu)
                    new_filename = f"{safe_so_ky_hieu}_{original_name}"
                    file_path = os.path.join(DOWNLOAD_DIR, new_filename)
                    
                    # Lưu file PDF
                    await download.save_as(file_path)
                    
                    # --- TẠO FILE SIÊU DỮ LIỆU (METADATA) ---
                    # File này dùng để truyền thông tin vào DB Vector cho việc tra cứu sau này
                    meta_path = file_path + ".meta.json"
                    meta_data = {
                        "so_ky_hieu": so_ky_hieu,
                        "ngay_ban_hanh": ngay_ban_hanh,
                        "trich_yeu": trich_yeu,
                        "file_goc": original_name
                    }
                    with open(meta_path, "w", encoding="utf-8") as mf:
                        json.dump(meta_data, mf, ensure_ascii=False, indent=4)
                        
                    print(f"   └─ Đã lưu: {new_filename} (Kèm file .meta.json)")
                    
                except Exception as e:
                    print(f"   ⚠️ Lỗi khi tải đính kèm thứ {link_idx + 1}: {e}")

        await browser.close()
        print("\n✅ Đóng trình duyệt. Kết thúc kịch bản Demo.")

if __name__ == "__main__":
    asyncio.run(download_demo_documents())
