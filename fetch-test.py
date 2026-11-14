from pymongo import MongoClient
import pandas as pd

# 1. MongoDB 연결
client = MongoClient("mongodb://localhost:27017/")
db = client["fnguide_db"]
collection = db["stock_tables"]

# 2. 테스트할 종목 코드 지정 (예: 삼성전자 '005930')
target_code = "005930"
target_date = "2025/12"

# MongoDB에서 해당 문서 조회
doc = collection.find_one({"_id": target_code})

if doc and "parsed_data" in doc:
    # Snapshot Annual parsed 데이터 가져오기
    snapshot_annual = doc.get("parsed_data", {}).get("snapshot", {}).get("annual", {})
    
    if snapshot_annual:
        print(f"[{doc.get('code')}] {doc.get('name')} - Snapshot 연간 ({target_date}) 데이터 추출 결과\n")
        print("=" * 55)
        print(f"{'계정과목':<25} | {target_date} 값")
        print("=" * 55)
        
        extracted_data = {}
        
        # 각 계정과목별로 2025/12 수치 추출
        for account, values in snapshot_annual.items():
            # values가 딕셔너리 형태이고 해당 날짜 키가 존재하는지 확인
            if isinstance(values, dict) and target_date in values:
                val = values[target_date]
                extracted_data[account] = val
                
                # 천단위 콤마 포맷팅 (숫자인 경우)
                formatted_val = f"{val:,.2f}" if isinstance(val, float) else (f"{val:,}" if isinstance(val, int) else str(val))
                print(f"{account:<25} | {formatted_val}")
        
        print("=" * 55)
        
        # 3. Pandas DataFrame으로 깔끔하게 정리하여 보기 (선택사항)
        df_2025 = pd.DataFrame(list(extracted_data.items()), columns=["계정과목", target_date])
        print("\n[Pandas DataFrame 변환 결과]")
        print(df_2025.to_string(index=False))

    else:
        print("❌ 'parsed_data.snapshot.annual' 데이터가 비어있습니다. DB 저장을 재확인해주세요.")
else:
    print(f"❌ DB에서 종목 코드 [{target_code}] 문서를 찾을 수 없습니다.")