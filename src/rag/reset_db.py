import os
from dotenv import load_dotenv
from qdrant_client import QdrantClient

load_dotenv()

print("🔌 Đang kết nối CSDL Vector Qdrant...")
qdrant = QdrantClient(
    host=os.getenv("QDRANT_HOST", "localhost"), 
    port=int(os.getenv("QDRANT_PORT", 6333)),
    api_key=os.getenv("QDRANT_API_KEY"),
    https=False 
)

collection_name = os.getenv("QDRANT_COLLECTION", "qlvb_docs")

print(f"🗑️ Đang tiến hành xóa toàn bộ dữ liệu của '{collection_name}'...")
try:
    # Lệnh xóa toàn bộ collection
    success = qdrant.delete_collection(collection_name=collection_name)
    if success:
        print(f"✅ Đã dọn sạch CSDL! Lần tới chạy ingest.py, hệ thống sẽ tạo lại từ đầu.")
    else:
        print(f"⚠️ Không thể xóa (Có thể collection không tồn tại).")
except Exception as e:
    print(f"⚠️ Đã xảy ra lỗi: {e}")
