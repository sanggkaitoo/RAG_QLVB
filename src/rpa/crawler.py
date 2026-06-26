import os
import json
import re
import asyncio
import subprocess
import sys
from pathlib import Path

# --- CHỈ ĐƯỜNG CHO PYTHON THẤY THƯ MỤC GỐC ---
project_root = str(Path(__file__).parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
# ---------------------------------------------

from dotenv import load_dotenv
from playwright.async_api import async_playwright
from src.rpa.auth import login_with_retry

load_dotenv()

DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/home/qlvb/rag_qlvb/data/downloads")
HISTORY_FILE = os.path.join(DOWNLOAD_DIR, "downloaded_records.json")

def load_download_history() -> set:
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            try: return set(json.load(f))
            except json.JSONDecodeError: return set()
    return set()

def save_download_history(history_set: set):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(list(history_set), f, ensure_ascii=False, indent=4)

def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()

async def run_pipeline():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    history_set = load_download_history()
    
    TARGET_URL = "https://egov1.laocai.gov.vn/document/xem-di-index?statustype=published&type=vanbandi"
    
    # --- THÊM GIỚI HẠN TẢI 100 VĂN BẢN ĐỂ TEST ---
    TARGET_DOC_LIMIT = 10
    total_downloaded = 0
    # ---------------------------------------------

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False) 
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()

        username = os.getenv("QLVB_USERNAME")
        password = os.getenv("QLVB_PASSWORD")

        print("🔄 Bắt đầu luồng tự động tải văn bản...")
        success = await login_with_retry(page, username, password)
        if not success:
            print("❌ Đăng nhập thất bại. Hủy tiến trình.")
            await browser.close()
            return

        print(f"\n🌐 Đang truy cập danh sách: {TARGET_URL}")
        await page.goto(TARGET_URL)
        await page.wait_for_selector("table tbody tr", timeout=15000)

        print("\n🔄 Đang thao tác sắp xếp văn bản theo 'Ngày ban hành'...")
        try:
            sort_icon = page.locator("table thead th").nth(5).locator("i").first
            await sort_icon.click()
            print("   - Đã click lần 1. Đang đợi bảng tải lại...")
            await page.wait_for_timeout(3000)
            
            await sort_icon.click()
            print("   - Đã click lần 2. Đang đợi bảng tải lại...")
            await page.wait_for_timeout(3000)
            print("✅ Đã sắp xếp văn bản thành công. Sẵn sàng quét dữ liệu!")
        except Exception as e:
            print(f"⚠️ Không thể click sắp xếp (Bot sẽ tiếp tục quét với thứ tự mặc định): {e}")

        page_num = 1
        has_next_page = True
        
        while has_next_page:
            print(f"\n📄 Đang xử lý Trang {page_num}...")
            await page.wait_for_timeout(2000)
            
            rows = await page.locator("table tbody tr").all()
            new_docs_in_page = 0
            
            for row in rows:
                # KIỂM TRA ĐIỀU KIỆN DỪNG: Đã tải đủ 100 văn bản
                if total_downloaded >= TARGET_DOC_LIMIT:
                    break

                cells = row.locator("td")
                if await cells.count() < 6: continue
                
                trich_yeu = (await cells.nth(1).inner_text()).strip()
                so_ky_hieu = (await cells.nth(3).inner_text()).strip()
                ngay_ban_hanh = (await cells.nth(5).inner_text()).strip()
                
                if not so_ky_hieu: continue

                if so_ky_hieu in history_set:
                    continue
                
                new_docs_in_page += 1
                total_downloaded += 1 # Tăng biến đếm tổng số văn bản đã tải
                
                print(f"[{total_downloaded}/{TARGET_DOC_LIMIT}] ⬇️ Đang tải: [{so_ky_hieu}] - {trich_yeu[:40]}...")

                # =======================================================
                # CẬP NHẬT MỚI: XỬ LÝ HỘP THOẠI (MODAL) TẢI FILE
                # =======================================================
                try:
                    # 1. Tìm và click vào button ở cột cuối cùng
                    button_locator = cells.last.locator("button")
                    if await button_locator.count() > 0:
                        await button_locator.first.click()
                    else:
                        await cells.last.click() # Chạm trực tiếp vào ô nếu không thấy thẻ button

                    # 2. Đợi hộp thoại ngb-modal-window bật lên
                    modal = page.locator("ngb-modal-window")
                    await modal.wait_for(state="visible", timeout=8000)
                    await page.wait_for_timeout(1000) # Nghỉ 1 giây để danh sách file load xong

                    # 3. Lấy tất cả các thẻ link <a> nằm trong hộp thoại
                    file_links = await modal.locator("a").all()
                    for link_idx, link in enumerate(file_links):
                        try:
                            # Hứng sự kiện tải file sinh ra từ việc click
                            async with page.expect_download(timeout=15000) as download_info:
                                await link.click()
                            
                            download = await download_info.value
                            new_filename = f"{sanitize_filename(so_ky_hieu)}_{download.suggested_filename}"
                            file_path = os.path.join(DOWNLOAD_DIR, new_filename)
                            
                            await download.save_as(file_path)
                            
                            meta_path = file_path + ".meta.json"
                            with open(meta_path, "w", encoding="utf-8") as mf:
                                json.dump({
                                    "so_ky_hieu": so_ky_hieu,
                                    "ngay_ban_hanh": ngay_ban_hanh,
                                    "trich_yeu": trich_yeu,
                                    "file_goc": download.suggested_filename
                                }, mf, ensure_ascii=False, indent=2)
                        except Exception as e:
                            print(f"   ⚠️ Lỗi tải file đính kèm bên trong modal: {e}")

                    # 4. Tắt hộp thoại bằng cách nhấn phím ESC
                    await page.keyboard.press("Escape")
                    await modal.wait_for(state="hidden", timeout=5000)

                except Exception as e:
                    print(f"   ⚠️ Lỗi khi mở hộp thoại tải file: {e}")
                    # Cứu hộ: Nếu bị kẹt modal trên màn hình, ép nhấn ESC để cố thoát ra
                    await page.keyboard.press("Escape")
                # =======================================================

                history_set.add(so_ky_hieu)
                save_download_history(history_set)

            # KIỂM TRA LẠI: Nếu vòng lặp for vừa bị break do chạm ngưỡng 100
            if total_downloaded >= TARGET_DOC_LIMIT:
                print(f"\n🎉 Đã tải đủ {TARGET_DOC_LIMIT} văn bản để test. Dừng lướt web.")
                has_next_page = False
                break

            if new_docs_in_page == 0 and page_num > 1:
                print("🛑 Đã quét tới các văn bản cũ của ngày hôm trước. Dừng lướt web để tiết kiệm tài nguyên.")
                break

            next_li = page.locator("li.page-item", has=page.locator("a", has_text="›")).first
            class_attr = await next_li.get_attribute("class")
            if class_attr and "disabled" in class_attr:
                print("🏁 Đã đến trang cuối cùng của hệ thống.")
                has_next_page = False
            else:
                print("➡️ Đang lật sang trang tiếp theo...")
                await next_li.click()
                await page.wait_for_timeout(3000) 
                page_num += 1

        print("✅ Hoàn tất tải file. Đang đóng trình duyệt...")
        await browser.close()

    print("\n🚀 BẮT ĐẦU KÍCH HOẠT HỆ THỐNG XỬ LÝ AI ĐỂ ĐỌC FILE...")
    python_executable = sys.executable
    ingest_script = os.path.join(project_root, "src", "rag", "ingest.py")
    subprocess.run([python_executable, ingest_script])
    print("\n🎉 TOÀN BỘ QUY TRÌNH ĐÃ HOÀN TẤT!")

if __name__ == "__main__":
    asyncio.run(run_pipeline())