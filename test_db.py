import psycopg2

# PostgreSQL 접속 정보
conn = psycopg2.connect(
    host="172.21.109.51",       # 도커 외부에서 실행 시: localhost / 내부에서는: 서비스 이름 (예: db)
    port=5432,
    dbname="web_db",
    user="youmh",
    password="you1288"
)

# 커서 열기
cur = conn.cursor()

# SELECT 쿼리 실행
cur.execute("SELECT id, name, email FROM users ORDER BY id")

# 결과 가져오기
rows = cur.fetchall()

# 출력
print("🧾 [사용자 목록]")
for row in rows:
    print(f"ID: {row[0]}, 이름: {row[1]}, 이메일: {row[2]}")

# 리소스 정리
cur.close()
conn.close()
