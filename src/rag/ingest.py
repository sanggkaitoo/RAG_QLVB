import os
import json
import uuid
import subprocess
from dotenv import load_dotenv
from langchain.text_splitter import RecursiveCharacterTextSplitter
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from sentence_transformers import SentenceTransformer

# Thư viện đọc file đa định dạng
import fitz
import docx
import pandas as pd

# Thư viện cho OCR (Ảnh và PDF Scan)
import pytesseract
from pdf2image import convert_from_path
from PIL import Image

load_dotenv()

DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/opt/qlvb_ai/data/downloads")

print("\n[INGEST PIPELINE] ⏳ Đang tải mô hình ngôn ngữ BGE-M3...")
embedding_model = SentenceTransformer(os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3"))
vector_dim = embedding_model.get_sentence_embedding_dimension()

print("[INGEST PIPELINE] 🔌 Đang kết nối CSDL Vector Qdrant...")
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

# ================= CÁC HÀM BÓC TÁCH DỮ LIỆU =================

def extract_pdf(file_path):
    text = ""
    try:
        doc = fitz.open(file_path)
        for page in doc:
            text += page.get_text("text") + "\n"
        doc.close()
    except Exception as e:
        print(f"Lỗi đọc text PDF {file_path}: {e}")
        
    text = text.strip()
    
    # Kích hoạt OCR nếu PDF là dạng ảnh scan (chữ lấy được quá ít)
    if len(text) < 50:
        print("   👁️ Phát hiện PDF dạng ảnh scan. Đang kích hoạt module OCR đọc chữ tiếng Việt...")
        try:
            images = convert_from_path(file_path)
            ocr_text = ""
            for i, img in enumerate(images):
                print(f"      - Đang quét OCR trang {i + 1}/{len(images)}...")
                page_text = pytesseract.image_to_string(img, lang='vie')
                ocr_text += page_text + "\n"
            text = ocr_text.strip()
        except Exception as e:
            print(f"   ⚠️ Lỗi trong quá trình OCR PDF: {e}")
            
    return text

def extract_docx(file_path):
    doc = docx.Document(file_path)
    return "\n".join([p.text for p in doc.paragraphs]).strip()

def extract_doc(file_path):
    temp_dir = "/tmp/qlvb_convert"
    os.makedirs(temp_dir, exist_ok=True)
    subprocess.run(['libreoffice', '--headless', '--convert-to', 'docx', file_path, '--outdir', temp_dir], capture_output=True)
    base_name = os.path.basename(file_path)
    docx_path = os.path.join(temp_dir, base_name + "x")
    if os.path.exists(docx_path):
        text = extract_docx(docx_path)
        os.remove(docx_path)
        return text
    return ""

def extract_excel(file_path):
    text = ""
    xls = pd.ExcelFile(file_path)
    for sheet_name in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet_name)
        if df.empty:
            continue
        text += f"\n--- Bảng dữ liệu (Sheet: {sheet_name}) ---\n"
        text += df.to_csv(index=False, sep='\t') + "\n"
    return text.strip()

def extract_image(file_path):
    """Đọc chữ trực tiếp từ các file ảnh (.jpg, .png, .jpeg)"""
    print(f"   🖼️ Đang quét OCR file ảnh: {os.path.basename(file_path)}...")
    try:
        img = Image.open(file_path)
        text = pytesseract.image_to_string(img, lang='vie')
        return text.strip()
    except Exception as e:
        print(f"   ⚠️ Lỗi trong quá trình OCR ảnh: {e}")
        return ""

def extract_text(file_path):
    ext = file_path.lower().split('.')[-1]
    try:
        if ext == 'pdf': return extract_pdf(file_path)
        elif ext == 'docx': return extract_docx(file_path)
        elif ext == 'doc': return extract_doc(file_path)
        elif ext in ['xlsx', 'xls']: return extract_excel(file_path)
        elif ext in ['png', 'jpg', 'jpeg', 'bmp', 'tiff']: return extract_image(file_path)
    except Exception as e:
        print(f"⚠️ Lỗi trích xuất chữ từ {file_path}: {e}")
    return ""

# ================= KỊCH BẢN CHÍNH =================

def process_documents():
    files = [f for f in os.listdir(DOWNLOAD_DIR) if not f.endswith(".json")]
    if not files:
        print("[INGEST PIPELINE] 📭 Không có tài liệu mới nào cần xử lý.")
        return

    for file_name in files:
        file_path = os.path.join(DOWNLOAD_DIR, file_name)
        meta_path = file_path + ".meta.json"
        
        print(f"\n⚙️ Đang xử lý: {file_name}")
        
        meta_data = {"so_ky_hieu": "N/A", "ngay_ban_hanh": "N/A", "trich_yeu": "N/A"}
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as mf:
                meta_data = json.load(mf)

        full_text = extract_text(file_path)
        if not full_text:
            print(f"⚠️ Cảnh báo: File {file_name} rỗng hoặc OCR không đọc được. Tiến hành xóa file rác.")
            os.remove(file_path)
            if os.path.exists(meta_path): os.remove(meta_path)
            continue
            
        chunks = text_splitter.split_text(full_text)
        total_chunks = len(chunks)
        print(f"✂️ Băm thành {total_chunks} mảnh ghép dữ liệu.")
        
        points = []
        for chunk in chunks:
            vector = embedding_model.encode(chunk).tolist()
            point_id = str(uuid.uuid4())
            
            payload = {
                "text": chunk,
                "so_ky_hieu": meta_data.get("so_ky_hieu"),
                "ngay_ban_hanh": meta_data.get("ngay_ban_hanh"),
                "trich_yeu": meta_data.get("trich_yeu"),
                "file_name": file_name
            }
            points.append(PointStruct(id=point_id, vector=vector, payload=payload))
            
        batch_size = 100
        for i in range(0, len(points), batch_size):
            batch = points[i:i + batch_size]
            qdrant.upsert(collection_name=collection_name, points=batch)
            print(f"   => Đã lưu lô {i + len(batch)}/{total_chunks} vào Qdrant...")
            
        print(f"✅ Hoàn tất lưu toàn bộ vector của [{meta_data.get('so_ky_hieu')}].")
        
        try:
            os.remove(file_path)
            if os.path.exists(meta_path):
                os.remove(meta_path)
            print(f"🗑️ Đã dọn dẹp xóa file gốc để giải phóng bộ nhớ.")
        except Exception as e:
            print(f"⚠️ Không thể xóa file {file_name}: {e}")

if __name__ == "__main__":
    process_documents()
