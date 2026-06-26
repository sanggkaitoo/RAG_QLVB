import os
import json
import uuid
import subprocess
import re

from click import prompt
from dotenv import load_dotenv
from langchain.text_splitter import RecursiveCharacterTextSplitter
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from sentence_transformers import SentenceTransformer
from openai import OpenAI
from loguru import logger
import fitz
import docx
import pandas as pd
import pytesseract
import cv2

load_dotenv()

# Lấy thư mục gốc của dự án. Mặc định tự động nhận diện dựa trên vị trí file (lùi lại 2 cấp từ src/rag/ingest.py)
DEFAULT_BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
BASE_DIR = os.getenv("BASE_DIR", DEFAULT_BASE_DIR)

# --- CẤU HÌNH GHI LOG ---
log_path = os.path.join(BASE_DIR, "logs", "ingest_api.log")
logger.add(
    log_path,
    rotation="10 MB",   # Cắt sang file mới khi file đạt 10MB (Mở bằng Notepad/VSCode không bị lag)
    retention=10,       # Xóa file cũ, chỉ giữ lại tối đa 10 file gần nhất (Tổng max 100MB)
    encoding="utf-8",
    enqueue=True        # Đảm bảo ghi log an toàn khi có nhiều tiến trình chạy
)

# Thư mục tải về sẽ nối từ BASE_DIR nếu chưa khai báo DOWNLOAD_DIR trong .env
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", os.path.join(BASE_DIR, "data", "downloads"))

print(f"\n[INGEST PIPELINE] 📂 Thư mục dữ liệu đang trỏ đến: {DOWNLOAD_DIR}")
print("[INGEST PIPELINE] ⏳ Đang khởi tạo mô hình và kết nối CSDL...")
embedding_model = SentenceTransformer(os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3"))
vector_dim = embedding_model.get_sentence_embedding_dimension()

# --- SỬ DỤNG HOÀN TOÀN QWEN QUA OPENROUTER ---
openai_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY")
)
LLM_MODEL = os.getenv("OPENROUTER_MODEL", "qwen/qwen-2.5-72b-instruct")

qdrant = QdrantClient(
    host=os.getenv("QDRANT_HOST", "localhost"), 
    port=int(os.getenv("QDRANT_PORT", 6333)),
    api_key=os.getenv("QDRANT_API_KEY"),
    https=False,
    timeout=60.0 
)

collection_name = os.getenv("QDRANT_COLLECTION", "qlvb_docs")

try:
    qdrant.get_collection(collection_name)
except:
    qdrant.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(size=vector_dim, distance=Distance.COSINE)
    )

text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)

def generate_metadata_with_qwen(trich_yeu, file_text_snippet):
    """Sử dụng Qwen để phân loại chuẩn Nghị định 30/2020/NĐ-CP"""
    
    prompt = f"""
    Bạn là chuyên viên văn thư lưu trữ của Sở Khoa học và Công nghệ.
    Đọc trích yếu và nội dung văn bản, phân loại và trích xuất thẻ (tags) ĐÚNG định dạng JSON.
    
    1. QUY TẮC PHÂN LOẠI "loai_van_ban" (BẮT BUỘC CHỌN 1 TRONG 29 LOẠI SAU):
    ["Nghị quyết", "Quyết định", "Chỉ thị", "Quy chế", "Quy định", "Thông cáo", "Thông báo", "Hướng dẫn", "Chương trình", "Kế hoạch", "Phương án", "Đề án", "Dự án", "Báo cáo", "Biên bản", "Tờ trình", "Hợp đồng", "Công văn", "Công điện", "Bản ghi nhớ", "Bản thỏa thuận", "Giấy ủy quyền", "Giấy mời", "Giấy giới thiệu", "Giấy nghỉ phép", "Phiếu gửi", "Phiếu chuyển", "Phiếu báo", "Thư công"]
    (Lưu ý: Viết in hoa chữ cái đầu tiên y hệt danh sách trên. Nếu không xác định được rõ, mặc định là "Công văn").

    2. QUY TẮC PHÂN LOẠI "cap_ban_hanh":
    Chọn 1 trong: ["trung_uong", "tinh", "so_ban_nganh", "xa_phuong_huyen", "khac"]

    3. QUY TẮC CHỌN TAGS CHUYÊN ĐỀ (Mảng "tags" 3-6 từ khóa):
    - Tầng Chủ trương: Nếu nhắc đến "Đề án 06" hoặc "ĐA 06" -> Gán tag "Đề án 06".
    - Tầng Lĩnh vực: Buộc có 1 trong ["Khoa học", "Công nghệ", "Chuyển đổi số", "Đổi mới sáng tạo"].
    - Tầng Chuyên đề cụ thể: ["hạ tầng số", "chính quyền số", "kinh tế số", "xã hội số", "nghiên cứu khoa học", "sở hữu trí tuệ", "khởi nghiệp"...]

    VÍ DỤ MẪU:
    - Đầu vào: BC tiến độ phủ sóng cáp quang, 5G.
      JSON: {{"loai_van_ban": "Báo cáo", "cap_ban_hanh": "tinh", "tags": ["Nghị quyết 57-NQ/TW", "Chuyển đổi số", "hạ tầng số", "mạng 5G", "viễn thông"]}}

    TUYỆT ĐỐI CHỈ TRẢ VỀ CHUỖI JSON, KHÔNG BÌNH LUẬN THÊM.
    
    --- THÔNG TIN VĂN BẢN ---
    Trích yếu: {trich_yeu}
    Nội dung: {file_text_snippet[:1500]}
    """
    
    try:
        logger.info(f"\n========== [REQUEST - INGEST] ==========\n{prompt}\n========================================")

        response = openai_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        
        # --- LỚP PHÒNG THỦ LỖI API CHẶN SUBSCRIPTABLE ---
        if not response or not hasattr(response, 'choices') or not response.choices:
            logger.error("API Error: OpenRouter không trả về kết quả.")
            print(f"⚠️ Lỗi API: OpenRouter không trả về kết quả (Có thể do giới hạn Rate Limit).")
            return {"loai_van_ban": "khac", "cap_ban_hanh": "khac", "tags": []}
            
        content = response.choices[0].message.content

        logger.success(f"\n========== [RESPONSE - INGEST] ==========\n{content}\n=========================================")

        if content is None:
            print("⚠️ Lỗi API: Nội dung trả về rỗng (Bị chặn bởi filter hoặc model đang quá tải).")
            return {"loai_van_ban": "khac", "cap_ban_hanh": "khac", "tags": []}
        # ------------------------------------------------
        
        raw_output = content.strip()
        
        # Bóc tách JSON an toàn. 
        json_match = re.search(r'`{3}(?:json)?\s*({.*?})\s*`{3}', raw_output, re.DOTALL)
        if json_match:
            raw_output = json_match.group(1)
            
        return json.loads(raw_output)
        
    except json.JSONDecodeError:
        print(f"⚠️ Lỗi JSON: AI trả về định dạng không chuẩn ({raw_output})")
        return {"loai_van_ban": "khac", "cap_ban_hanh": "khac", "tags": []}
    except Exception as e:
        print(f"⚠️ Lỗi xử lý AI: {e}")
        return {"loai_van_ban": "khac", "cap_ban_hanh": "khac", "tags": []}

# --- CÁC HÀM TRÍCH XUẤT ---
def extract_pdf(file_path):
    doc = fitz.open(file_path)
    return "\n".join([page.get_text("text") for page in doc]).strip()

def extract_docx(file_path):
    doc = docx.Document(file_path)
    return "\n".join([p.text for p in doc.paragraphs]).strip()

def extract_doc(file_path):
    # Cập nhật thư mục tạm thời để nằm trong BASE_DIR thay vì fix cứng ở /tmp/
    temp_dir = os.path.join(BASE_DIR, "data", "tmp")
    os.makedirs(temp_dir, exist_ok=True)
    subprocess.run(['libreoffice', '--headless', '--convert-to', 'docx', file_path, '--outdir', temp_dir], capture_output=True)
    docx_path = os.path.join(temp_dir, os.path.basename(file_path) + "x")
    if os.path.exists(docx_path):
        text = extract_docx(docx_path)
        os.remove(docx_path)
        return text
    return ""

def extract_excel(file_path):
    xls = pd.ExcelFile(file_path)
    text = ""
    for sheet_name in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet_name)
        if not df.empty: text += f"\n--- Sheet: {sheet_name} ---\n" + df.to_csv(index=False, sep='\t') + "\n"
    return text.strip()

def extract_txt(file_path):
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read().strip()

def extract_jpg(file_path):
    img = cv2.imread(file_path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return pytesseract.image_to_string(gray, lang="vie").strip()

def extract_text(file_path):
    ext = file_path.lower().split('.')[-1]
    try:
        if ext == 'pdf': return extract_pdf(file_path)
        elif ext == 'docx': return extract_docx(file_path)
        elif ext == 'doc': return extract_doc(file_path)
        elif ext in ['xlsx', 'xls']: return extract_excel(file_path)
        elif ext == 'txt': return extract_txt(file_path)
        elif ext in ['jpg', 'jpeg', 'png']: return extract_jpg(file_path)
    except Exception as e: print(f"⚠️ Lỗi trích xuất từ {file_path}: {e}")
    return ""

def process_documents():
    if not os.path.exists(DOWNLOAD_DIR):
        print(f"[INGEST PIPELINE] 📭 Thư mục {DOWNLOAD_DIR} chưa tồn tại, đang tạo mới...")
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        return

    files = [f for f in os.listdir(DOWNLOAD_DIR) if not f.endswith(".json")]
    if not files:
        print("[INGEST PIPELINE] 📭 Không có tài liệu mới nào cần xử lý.")
        return
        
    for file_name in files:
        file_path = os.path.join(DOWNLOAD_DIR, file_name)
        meta_path = file_path + ".meta.json"
        
        meta_data = {"so_ky_hieu": "N/A", "ngay_ban_hanh": "N/A", "trich_yeu": "N/A", "file_goc": "N/A"}
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as mf: meta_data = json.load(mf)

        full_text = extract_text(file_path)
        if not full_text:
            os.remove(file_path)
            if os.path.exists(meta_path): os.remove(meta_path)
            continue
        
        print(f"🤖 Đang gán nhãn cho: [{meta_data.get('so_ky_hieu')}] bằng Qwen...")
        ai_tags = generate_metadata_with_qwen(meta_data.get('trich_yeu'), full_text)
        meta_data.update(ai_tags)
        print(f"🏷️ Metadata: {ai_tags}")

        chunks = text_splitter.split_text(full_text)
        points = []
        for chunk in chunks:
            payload = {
                "text": chunk,
                "so_ky_hieu": meta_data.get("so_ky_hieu"),
                "ngay_ban_hanh": meta_data.get("ngay_ban_hanh"),
                "trich_yeu": meta_data.get("trich_yeu"),
                "file_name": meta_data.get("file_goc", file_name),
                "loai_van_ban": meta_data.get("loai_van_ban", "khac"),
                "cap_ban_hanh": meta_data.get("cap_ban_hanh", "khac"),
                "tags": meta_data.get("tags", [])
            }
            points.append(PointStruct(id=str(uuid.uuid4()), vector=embedding_model.encode(chunk).tolist(), payload=payload))
            
        for i in range(0, len(points), 100):
            qdrant.upsert(collection_name=collection_name, points=points[i:i + 100])
            
        print(f"✅ Hoàn tất: {meta_data.get('so_ky_hieu')}")
        os.remove(file_path)
        if os.path.exists(meta_path): os.remove(meta_path)

if __name__ == "__main__":
    process_documents()