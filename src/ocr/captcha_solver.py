import cv2
import numpy as np
import pytesseract
import re

def solve_captcha_tesseract(image_bytes: bytes) -> str:
    # 1. Đọc ảnh thô ở chế độ màu (để giữ thông tin độ tương phản nền và chữ)
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    # 2. Phóng to ảnh gấp 3 lần để nét chữ dày hẳn lên, tạo lợi thế khi lọc nhiễu
    img = cv2.resize(img, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)

    # 3. Chuyển sang ảnh xám (Grayscale)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # 4. LỌC TRUNG VỊ (Kỹ thuật cốt lõi): 
    # Kernel size = 5 sẽ xóa sạch các đường kẻ sọc mảnh mà không làm đứt nét chữ
    blur = cv2.medianBlur(gray, 5)

    # 5. Nhị phân hóa (Đen/Trắng) bằng phương pháp Otsu
    # Sau bước này, chữ sẽ màu trắng, nền màu đen (Tesseract rất thích định dạng này)
    _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # 6. Cấu hình Tesseract
    # --psm 7: Coi toàn bộ ảnh là một dòng text duy nhất
    # whitelist: Ép AI chỉ nhìn ra chữ và số
    config = '--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
    
    # Thực thi nhận diện trên bức ảnh đã làm sạch
    raw_text = pytesseract.image_to_string(thresh, config=config)

    # 7. Xử lý logic hậu kiểm (Dựa trên quy luật vàng của hệ thống)
    # Xóa toàn bộ dấu cách, khoảng trắng, ký tự rác nếu có
    clean_text = re.sub(r'[^a-zA-Z0-9]', '', raw_text)
    
    # Vì Captcha luôn có 3 ký tự:
    # Nếu Tesseract nhìn ra 4-5 ký tự (do bóng mờ ở viền ảnh), ta cắt lấy 3 ký tự đầu/cuối
    # Ở đây thường viền phải hay bị nhiễu dấu móc (như trong ảnh y2r), ta ưu tiên 3 ký tự đầu
    if len(clean_text) > 3:
        clean_text = clean_text[:3]

    return clean_text
