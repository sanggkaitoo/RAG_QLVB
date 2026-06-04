import os
import time
import asyncio
from playwright.async_api import Page

async def login_with_retry(page: Page, username: str, password: str, max_retries: int = 5) -> bool:
    """
    Vòng lặp tự động đăng nhập (Bán tự động: Nhập Captcha thủ công qua Terminal).
    """
    print(f"Bắt đầu quá trình đăng nhập (Chế độ thủ công - Tối đa {max_retries} lần thử)...")
    
    os.makedirs("logs/captchas", exist_ok=True)
    
    qlvb_url = os.getenv("QLVB_URL")
    await page.goto(qlvb_url)
    await page.wait_for_timeout(2000)

    for attempt in range(1, max_retries + 1):
        print(f"\n--- Thử đăng nhập lần {attempt}/{max_retries} ---")
        
        # Chờ phần tử xuất hiện để đảm bảo trang đã load xong (đặc biệt sau khi reload báo lỗi)
        await page.wait_for_selector("input#usernameUserInput", timeout=10000)
        
        await page.fill("input#usernameUserInput", username)
        await page.fill("input#password", password)
        await page.wait_for_timeout(1000) 
        
        captcha_element = await page.query_selector("img#captchaImage")
        if not captcha_element:
            print("❌ Không tìm thấy ảnh Captcha trên trang. Vui lòng kiểm tra lại ID phần tử.")
            return False

        # Chụp ảnh Captcha
        captcha_bytes = await captcha_element.screenshot()
        file_path = "logs/captchas/manual_captcha.png"
        
        with open(file_path, "wb") as f:
            f.write(captcha_bytes)
            
        print(f"\n📸 Đã lưu ảnh Captcha mới nhất tại: {file_path}")
        print("👉 VUI LÒNG MỞ ẢNH LÊN XEM")
        
        prompt_text = "⌨️  Gõ mã Captcha bạn nhìn thấy (Hoặc để trống và nhấn Enter để đổi ảnh mới): "
        captcha_text = await asyncio.to_thread(input, prompt_text)
        captcha_text = captcha_text.strip()
        
        # CƠ CHẾ 1: Người dùng ấn Enter (Bỏ qua ảnh mờ)
        if not captcha_text:
            print("🔄 Đang tải lại mã Captcha mới...")
            # VÁ LỖI 1: Click trực tiếp vào ID trên giao diện thay vì dùng biến cũ
            await page.click("img#captchaImage")
            await page.wait_for_timeout(1500)
            continue

        print(f"Bắt đầu submit với mã: '{captcha_text}'")
        
        await page.fill("input#captcha", captcha_text)
        await page.click("button[type='submit']")
        
        try:
            await page.wait_for_url("**/home/default/1", timeout=5000)
            print("✅ Đăng nhập thành công! Hệ thống đã vào luồng làm việc.")
            return True
            
        except Exception:
            error_msg_element = await page.query_selector(".alert-danger")
            if error_msg_element:
                error_text = await error_msg_element.inner_text()
                print(f"⚠️ Lỗi từ hệ thống: {error_text.strip()}")
            else:
                print("⚠️ Đăng nhập không thành công (Khả năng cao do sai Captcha).")
            
            if attempt < max_retries:
                print("🔄 Đang chuẩn bị thử lại...")
                # VÁ LỖI 2: Dùng try-except khi click để tránh lỗi nếu trang đang trong quá trình reload
                try:
                    await page.click("img#captchaImage", timeout=3000)
                    await page.wait_for_timeout(1500)
                except Exception:
                    # Nếu trang web đã tự động tải lại (reload) sau khi báo lỗi, 
                    # ảnh Captcha đã tự mới, không cần click nữa.
                    pass

    print("❌ Đã thử tối đa số lần nhưng vẫn chưa đăng nhập được.")
    return False
