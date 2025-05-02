from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import psycopg2
from psycopg2.extras import RealDictCursor
from config import DB_CONFIG
import logging
import time
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta


# NOTE -   - 디바이스 조회 모델
# 디바이스 저장 모델 (등록/수정 요청용)
class DeviceCreate(BaseModel):
    system_id: int
    device_name: str
    ip_address: Optional[str] = None
    facility_name: Optional[str] = None
    port: Optional[int] = None
    is_lora: bool = False


# 시작잉
app = FastAPI()

# ✅ CORS 설정 추가 (Vue.js와 연결 시 필요) <- 로컬에서 돌릴 때
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 모든 도메인 허용 (보안 설정 필요)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 서버 로그 설정 
logging.basicConfig(filename="server.log", level=logging.INFO,
                    format="%(asctime)s - %(message)s")

# DB 연결 
def get_db_connection():
    return psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)

# API 호출 기본 경로
@app.get("/")
def root():
    return {
        "message": "IoT Device Management API",
        "swagger_url": "http://localhost:8000/docs",
        "redoc_url": "http://localhost:8000/redoc",
        "status": "running"
    }

# DB 상태 체크 
@app.get("/health")
def health_check():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.close()
        conn.close()
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        return {"status": "error", "database": str(e)}

# API 요청 로깅
@app.middleware("http")
async def log_requests(request, call_next):
    response = await call_next(request)
    logging.info(
        f"📡 {request.client.host} {request.method} {request.url} → {response.status_code}")
    return response

# API 응답 속도 측정
# -> Headers의 x-process-time에서 체크 가능, 평균 0.05초
@app.middleware("http")
async def add_process_time_header(request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(round(process_time, 4))
    return response


# ------------------- 여기서부터 디바이스 관리 API -----------------------------
# ANCHOR - 디바이스 CRUD
@app.post("/devices/")
def add_device(device: DeviceCreate):
    conn = get_db_connection()
    cursor = conn.cursor()

    # 빈 IP 주소 처리
    ip_address = device.ip_address if device.ip_address and device.ip_address.strip() else None

    # 빈 포트번호 처리 (문자열이 아니라면 변환)
    # port = device.port if device.port is not None else 22  # None이면 기본값 22

    try:
        cursor.execute("""
            INSERT INTO devices (system_id, device_name, ip_address, facility_name, port, is_lora)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id;
        """, (device.system_id, device.device_name, ip_address, device.facility_name, device.port, device.is_lora))

        new_id = cursor.fetchone()["id"]
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        cursor.close()
        conn.close()

    return {"id": new_id,  "message": "Device added successfully"}


# **디바이스 정보 수정 (`PUT /devices/{id}`)**
@app.put("/devices/{device_id}")
def update_device(device_id: int, device: DeviceCreate):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM devices WHERE id = %s", (device_id,))
    if cursor.fetchone() is None:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Device not found")

    update_fields = []
    values = []

    if device.system_id:
        update_fields.append("system_id = %s")
        values.append(device.system_id)
    if device.device_name:
        update_fields.append("device_name = %s")
        values.append(device.device_name)
    if device.ip_address is not None:
        update_fields.append("ip_address = %s")
        values.append(device.ip_address if device.ip_address.strip() else None)
    if device.facility_name:
        update_fields.append("facility_name = %s")
        values.append(device.facility_name)
    if device.port:
        update_fields.append("port = %s")
        values.append(device.port)
    if device.is_LoRa is not None:
        update_fields.append("is_LoRa = %s")
        values.append(device.is_LoRa)

    if not update_fields:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=400, detail="No fields to update")

    values.append(device_id)
    query = f"UPDATE devices SET {', '.join(update_fields)} WHERE id = %s"

    try:
        cursor.execute(query, tuple(values))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        cursor.close()
        conn.close()

    return {"message": f"Device {device_id} updated successfully"}

# **디바이스 삭제 (`DELETE /devices/{id}`)**
@app.delete("/devices/{device_id}")
def delete_device(device_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM devices WHERE id = %s", (device_id,))
    if cursor.fetchone() is None:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Device not found")

    try:
        cursor.execute("DELETE FROM devices WHERE id = %s", (device_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        cursor.close()
        conn.close()

    return {"message": f"Device {device_id} deleted successfully"}


# ------------------- 여기서부터 조회 API -----------------------------
# LINK - 등록된 디바이스 정보 조회
@app.get("/devices/")
def get_devices():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
                SELECT  s.id, s.name, s.description,
        		d.device_name, d.facility_name, d.ip_address, d.port, d.is_lora, d.is_use 
                FROM devices d
                JOIN systems s ON d.system_id = s.id
    """)
    devices = cursor.fetchall()
    cursor.close()
    conn.close()

    return {
        "total": len(devices),
        "devices": [
            {
                "system_id" : device["id"],
                "system_name" : device["description"],
                "device_name" : device["device_name"],
                "facility_name": device["facility_name"],
                "ip":device["ip_address"],
                "port" : device["port"],
                "is_lora" : device["is_lora"],
                "is_use" : device["is_use"]

            }
            for device in devices
        ]
    }

# LINK - 등록된 디바이스 시스템 개별로 조회
@app.get("/devices/{system_id}")
def get_devices(system_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT s.id AS system_id, s.name AS system_name, s.description,
               d.device_name, d.facility_name, d.ip_address, d.port, d.is_lora, d.is_use
        FROM devices d
        JOIN systems s ON d.system_id = s.id
        WHERE s.id = %s
    """, (system_id,))  #system_id 조건 추가

    # 결과 가져오기
    devices = cursor.fetchall()

    cursor.close()
    conn.close()

    # 만약 조회된 데이터가 없으면 404 에러 반환
    if not devices:
        raise HTTPException(status_code=404, detail=f"No devices found for system_id {system_id}")

    # 데이터를 JSON 형태로 변환
    devices_list = [
        {
            "system_id": device["system_id"],
            "system_name": device["system_name"],
            "system_description": device["description"],
            "device_name": device["device_name"],
            "facility_name": device["facility_name"],
            "ip": device["ip_address"],
            "port": device["port"],
            "is_lora": device["is_lora"],
            "is_use": device["is_use"]
        }
        for device in devices
    ]

    return {
        "total": len(devices_list),  # 전체 데이터 개수
        "devices": devices_list
    }


# LINK - 전체 디바이스 최신상태 조회
@app.get("/last")
def get_all_devices_status():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT d.id AS device_id, d.device_name, d.system_id, s.name AS system_name, 
               d.ip_address, d.is_lora, ds.ping_status, ds.ssh_status, ds.last_data_time
        FROM devices d
        JOIN systems s ON d.system_id = s.id
        LEFT JOIN device_status ds ON d.id = ds.device_id
    """)

    devices = cursor.fetchall()
    cursor.close()
    conn.close()

    # 데이터를 딕셔너리 리스트로 변환
    devices_list = [
        {
            # "index": idx + 1,
            # "device_id": device["device_id"], 
            "device_name": device["device_name"],
            "system_id": device["system_id"],
            "system_name": device["system_name"],
            "ip_address": None if device["is_lora"] else device["ip_address"],
            "is_lora": device["is_lora"],
            "ping_status": None if device["is_lora"] else device["ping_status"],
            "ssh_status": None if device["is_lora"] else device["ssh_status"],
            "last_data_time": device["last_data_time"]
        }
        for idx, device in enumerate(devices)
    ]

    return {
        "count": len(devices_list),  # 총 개수 추가
        "devices": devices_list
    }

# LINK - 시스템별 최신 데이터 조회
@app.get("/last/{system_id}")
def get_latest_device_status(system_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()

    # system_id 별 최신 상태 데이터를 가져옴
    cursor.execute("""
            SELECT  d.id AS device_id, d.device_name, d.system_id, s.name AS system_name, 
                    d.ip_address, d.is_lora, ds.ping_status, ds.ssh_status, ds.last_data_time
            FROM devices d
            JOIN systems s ON d.system_id = s.id
            LEFT JOIN device_status ds ON d.id = ds.device_id
            WHERE d.system_id = %s;
    """, (system_id,))

    devices = cursor.fetchall()
    cursor.close()
    conn.close()

    # LoRa 장비는 IP 및 SSH 상태를 null 처리
    for device in devices:
        if device["is_lora"]:
            device["ip_address"] = None
            device["ping_status"] = None
            device["ssh_status"] = None

    return devices

# LINK - IP통신하는 단말기 최신상태 조회
@app.get("/ip")
def get_ip_devices_status():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT d.device_name, d.system_id, d.facility_name, s.name AS system_name, s.description,
               d.ip_address, d.is_lora, ds.ping_status, ds.ssh_status, ds.last_data_time
        FROM devices d
        JOIN systems s ON d.system_id = s.id
        LEFT JOIN device_status ds ON d.id = ds.device_id
        WHERE d.ip_address IS NOT NULL
    """)
    devices = cursor.fetchall()
    cursor.close()
    conn.close()

    return {
        "total": len(devices),
        "devices": [
            {
                "device_name" : device["device_name"],
                "facility_name": device["facility_name"],
                "system_name" : device["system_name"],
                "description" : device["description"],
                "ip" : device["ip_address"],
                "ping_status" : device["ping_status"],
                "ssh_status" : device["ssh_status"]

            }
            for device in devices
        ]
    }


# LINK - LoRa 단말기 최신 상태 조회
@app.get("/lora")
def get_lora_devices_status():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT  d.device_name, d.facility_name, 
        		s.name AS system_name, s.description,
        		ds.ssh_status, ds.ssh_status, ds.last_data_time    		
        FROM devices d
        JOIN systems s ON d.system_id = s.id
        LEFT JOIN device_status ds ON d.id = ds.device_id
        WHERE d.is_lora = TRUE
    """)
    devices = cursor.fetchall()
    cursor.close()
    conn.close()

    return {
        "total": len(devices),
        "devices": [
            {
                "device_name" : device["device_name"],
                "facility_name": device["facility_name"],
                "system_name" : device["system_name"],
                "description" : device["description"],
                "last_data_time" : device["last_data_time"]
            }
            for device in devices
        ]
    }

# LINK - 이상 단말기 최신 현황
@app.get("/check")
def get_device_status_check():
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1️. 전체 단말기 개수 조회
    cursor.execute("SELECT COUNT(*) AS total_devices FROM devices;")
    total_devices = cursor.fetchone()["total_devices"]

    # 2️. 전체 Active 단말기 개수 조회
    cursor.execute("""
        SELECT COUNT(DISTINCT d.id) AS active_devices 
        FROM devices d
        LEFT JOIN device_status ds ON ds.device_id = d.id
        WHERE ds.last_data_time IS NOT NULL 
          AND ds.last_data_time >= NOW() - INTERVAL '5 days';
    """)
    active_devices = cursor.fetchone()["active_devices"]

    # 3️. 전체 Inactive 단말기 개수 조회
    inactive_devices = total_devices - active_devices  # 전체 개수에서 Active 단말기를 뺀 값

    # 4️. 시스템별 종합 현황 조회
    cursor.execute("""
        SELECT 
            s.id AS system_id,
            s.name AS system_name, 
            s.description,
            COUNT(DISTINCT d.id) AS system_total_devices,
            COUNT(DISTINCT CASE WHEN ds.last_data_time >= NOW() - INTERVAL '5 days' THEN d.id END) AS active_devices,
            COUNT(DISTINCT CASE WHEN ds.last_data_time IS NULL OR ds.last_data_time < NOW() - INTERVAL '5 days' THEN d.id END) AS inactive_devices
        FROM systems s
        LEFT JOIN devices d ON s.id = d.system_id
        LEFT JOIN device_status ds ON d.id = ds.device_id
        GROUP BY s.id, s.name, s.description
        ORDER BY s.id;
    """)
    
    system_status = cursor.fetchall()

    # 연결 종료
    cursor.close()
    conn.close()

    return {
        "total_devices": total_devices,
        "active_devices": active_devices,
        "inactive_devices": inactive_devices,
        "systems": system_status
    }


# LINK - 시스템별 이상한 단말기 최신현황
@app.get("/check/{system_id}")
def get_device_status_check_system(system_id: int):
    """
    특정 시스템(system_id)별 전체 단말기 개수, Active 단말기 개수, Inactive 단말기 개수 및 목록 조회
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # 시스템 정보 (system_name 가져오기)
    cursor.execute("SELECT description FROM systems WHERE id = %s;", (system_id,))
    system = cursor.fetchone()
    
    if not system:
        raise HTTPException(status_code=404, detail=f"System with ID {system_id} not found")
    
    system_name = system["description"]  

    # 전체 단말기 개수 (해당 시스템 내)
    cursor.execute("SELECT COUNT(*) AS total_devices FROM devices WHERE system_id = %s;", (system_id,))
    total_devices = cursor.fetchone()["total_devices"]

    # Active 단말기 개수 (최근 5일 내 데이터 수신)
    cursor.execute("""
        SELECT COUNT(*) AS active_devices 
        FROM devices d
        LEFT JOIN device_status ds ON ds.device_id = d.id
        WHERE d.system_id = %s
          AND ds.last_data_time IS NOT NULL 
          AND ds.last_data_time >= NOW() - INTERVAL '5 days';
    """, (system_id,))
    active_devices = cursor.fetchone()["active_devices"]

    # Inactive 단말기 개수 (5일 이상 데이터 없음 또는 NULL)
    inactive_devices = total_devices - active_devices  # 전체 개수에서 Active를 뺀 값

    # Inactive 단말기 목록 조회 (상세 정보)
    cursor.execute("""
        SELECT 
            d.id AS device_id,
            d.device_name,
            d.facility_name,
            d.ip_address,
            d.port,
            d.is_lora,
            ds.last_data_time,
            ds.ping_status,
            ds.ssh_status
        FROM devices d
        LEFT JOIN device_status ds ON ds.device_id = d.id
        WHERE d.system_id = %s
          AND (ds.last_data_time IS NULL 
               OR ds.last_data_time < NOW() - INTERVAL '5 days');
    """, (system_id,))
    inactive_list = cursor.fetchall()

    # Active 단말기 목록 조회 (상세 정보)
    cursor.execute("""
        SELECT 
            d.id AS device_id,
            d.device_name,
            d.facility_name,
            d.ip_address,
            d.port,
            d.is_lora,
            ds.last_data_time,
            ds.ping_status,
            ds.ssh_status
        FROM devices d
        LEFT JOIN device_status ds ON ds.device_id = d.id
        WHERE d.system_id = %s
          AND ds.last_data_time IS NOT NULL
          AND ds.last_data_time >= NOW() - INTERVAL '5 days';
    """, (system_id,))
    active_list = cursor.fetchall()

    # 연결 종료
    cursor.close()
    conn.close()

    return {
        "system_id": system_id,
        "system_name": system_name,
        "total_devices": total_devices,
        "active_devices": active_devices,
        "inactive_devices": inactive_devices,
        "inactive_device_list": inactive_list,
        "active_device_list": active_list
    }




#ANCHOR - 조회 시간
@app.get("/time")
def get_update_time():
    conn = get_db_connection()
    cursor = conn.cursor()

    # 미들웨어 돌아간 시간 조회 
    cursor.execute("""
     SELECT checked_at, total_devices, active_devices, inactive_devices
        FROM device_status_history
        ORDER BY checked_at DESC
        LIMIT 1
    """)
    update_time = cursor.fetchone()["checked_at"]


    # 연결 종료
    cursor.close()
    conn.close()

    return {
        "update_time": update_time
    }



#ANCHOR - 디바이스 현황 차트 조회 api
@app.get("/device-history")
def get_device_history():
    conn = get_db_connection()
    cursor = conn.cursor()

    # 최근 3일 데이터 조회
    days_ago = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
        SELECT checked_at, total_devices, active_devices, inactive_devices
        FROM device_status_history
        WHERE checked_at >= %s
        ORDER BY checked_at ASC
    """, (days_ago,))

    history_data = cursor.fetchall()
    

    # 연결 종료
    cursor.close()
    conn.close()

    return {
        "total": len(history_data),
        "history": history_data
    }



# replit setting
import uvicorn
if __name__ == "__main__" : 
    uvicorn.run("main:app", host="0.0.0.0", port=port)
