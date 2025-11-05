"""샘플 데이터 추가 스크립트 (테스트용)"""
from datetime import datetime, timedelta
import random
from database import Database

def add_sample_torrents():
    """샘플 토렌트 데이터 추가"""
    db = Database()
    session = db.get_session()
    
    sample_data = [
        {
            'title': '[Uncensored] SSNI-123 Beautiful Girl Creampie 美少女中出し',
            'source_id': 'sample_001',
            'source_site': 'sample_data',
            'magnet_link': 'magnet:?xt=urn:btih:SAMPLE123456789ABCDEF',
            'torrent_link': '',
            'size': '1.5 GiB',
            'size_bytes': 1610612736,
            'category': 'JAV',
            'censored': False,
            'country': 'JP',
            'seeders': 150,
            'leechers': 45,
            'downloads': 3200,
            'comments': 12,
            'upload_date': datetime.utcnow() - timedelta(days=1),
            'thumbnail_url': 'https://via.placeholder.com/300x200',
            'snapshot_urls': '',
            'genres': ['Creampie', 'Schoolgirl']
        },
        {
            'title': '[FC2-PPV] Amateur 素人 Cosplay コスプレ',
            'source_id': 'sample_002',
            'source_site': 'sample_data',
            'magnet_link': 'magnet:?xt=urn:btih:SAMPLE223456789ABCDEF',
            'torrent_link': '',
            'size': '2.3 GiB',
            'size_bytes': 2469606195,
            'category': 'JAV',
            'censored': False,
            'country': 'JP',
            'seeders': 200,
            'leechers': 80,
            'downloads': 5000,
            'comments': 25,
            'upload_date': datetime.utcnow() - timedelta(hours=12),
            'thumbnail_url': 'https://via.placeholder.com/300x200',
            'snapshot_urls': '',
            'genres': ['Cosplay', 'Amateur']
        },
        {
            'title': '国产精品 Chinese Homemade 麻豆传媒',
            'source_id': 'sample_003',
            'source_site': 'sample_data',
            'magnet_link': 'magnet:?xt=urn:btih:SAMPLE323456789ABCDEF',
            'torrent_link': '',
            'size': '850 MiB',
            'size_bytes': 891289600,
            'category': 'Asian',
            'censored': False,
            'country': 'CN',
            'seeders': 300,
            'leechers': 120,
            'downloads': 8000,
            'comments': 40,
            'upload_date': datetime.utcnow() - timedelta(hours=6),
            'thumbnail_url': 'https://via.placeholder.com/300x200',
            'snapshot_urls': '',
            'genres': ['Amateur', 'POV']
        },
        {
            'title': '[CARIB] Uncensored Threesome 無修正 3P',
            'source_id': 'sample_004',
            'source_site': 'sample_data',
            'magnet_link': 'magnet:?xt=urn:btih:SAMPLE423456789ABCDEF',
            'torrent_link': '',
            'size': '3.2 GiB',
            'size_bytes': 3435973836,
            'category': 'JAV',
            'censored': False,
            'country': 'JP',
            'seeders': 180,
            'leechers': 60,
            'downloads': 4500,
            'comments': 18,
            'upload_date': datetime.utcnow() - timedelta(days=2),
            'thumbnail_url': 'https://via.placeholder.com/300x200',
            'snapshot_urls': '',
            'genres': ['Threesome', 'Creampie']
        },
        {
            'title': 'Korean BJ 韩国主播 Webcam Show',
            'source_id': 'sample_005',
            'source_site': 'sample_data',
            'magnet_link': 'magnet:?xt=urn:btih:SAMPLE523456789ABCDEF',
            'torrent_link': '',
            'size': '1.1 GiB',
            'size_bytes': 1181116006,
            'category': 'Asian',
            'censored': False,
            'country': 'KR',
            'seeders': 250,
            'leechers': 90,
            'downloads': 6000,
            'comments': 30,
            'upload_date': datetime.utcnow() - timedelta(hours=3),
            'thumbnail_url': 'https://via.placeholder.com/300x200',
            'snapshot_urls': '',
            'genres': ['Solo', 'Masturbation']
        },
        {
            'title': '[HEYZO] Beautiful MILF 美熟女 無修正',
            'source_id': 'sample_006',
            'source_site': 'sample_data',
            'magnet_link': 'magnet:?xt=urn:btih:SAMPLE623456789ABCDEF',
            'torrent_link': '',
            'size': '1.8 GiB',
            'size_bytes': 1932735283,
            'category': 'JAV',
            'censored': False,
            'country': 'JP',
            'seeders': 120,
            'leechers': 40,
            'downloads': 2800,
            'comments': 15,
            'upload_date': datetime.utcnow() - timedelta(days=3),
            'thumbnail_url': 'https://via.placeholder.com/300x200',
            'snapshot_urls': '',
            'genres': ['MILF', 'Creampie']
        },
        {
            'title': '[Reducing Mosaic] IPX-456 Office Lady Blowjob',
            'source_id': 'sample_007',
            'source_site': 'sample_data',
            'magnet_link': 'magnet:?xt=urn:btih:SAMPLE723456789ABCDEF',
            'torrent_link': '',
            'size': '2.1 GiB',
            'size_bytes': 2254857830,
            'category': 'JAV',
            'censored': False,
            'country': 'JP',
            'seeders': 280,
            'leechers': 110,
            'downloads': 7200,
            'comments': 35,
            'upload_date': datetime.utcnow() - timedelta(hours=18),
            'thumbnail_url': 'https://via.placeholder.com/300x200',
            'snapshot_urls': '',
            'genres': ['Office', 'Blowjob']
        },
        {
            'title': 'Brazzers - Anal Threesome HD',
            'source_id': 'sample_008',
            'source_site': 'sample_data',
            'magnet_link': 'magnet:?xt=urn:btih:SAMPLE823456789ABCDEF',
            'torrent_link': '',
            'size': '4.5 GiB',
            'size_bytes': 4831838208,
            'category': 'Western',
            'censored': False,
            'country': 'US',
            'seeders': 400,
            'leechers': 150,
            'downloads': 12000,
            'comments': 50,
            'upload_date': datetime.utcnow() - timedelta(hours=8),
            'thumbnail_url': 'https://via.placeholder.com/300x200',
            'snapshot_urls': '',
            'genres': ['Anal', 'Threesome']
        },
        {
            'title': '[Tokyo Hot] Gangbang Creampie 輪姦中出し',
            'source_id': 'sample_009',
            'source_site': 'sample_data',
            'magnet_link': 'magnet:?xt=urn:btih:SAMPLE923456789ABCDEF',
            'torrent_link': '',
            'size': '2.8 GiB',
            'size_bytes': 3006477107,
            'category': 'JAV',
            'censored': False,
            'country': 'JP',
            'seeders': 160,
            'leechers': 55,
            'downloads': 3800,
            'comments': 20,
            'upload_date': datetime.utcnow() - timedelta(days=1, hours=12),
            'thumbnail_url': 'https://via.placeholder.com/300x200',
            'snapshot_urls': '',
            'genres': ['Gangbang', 'Creampie']
        },
        {
            'title': 'Thai Massage Happy Ending 泰式按摩',
            'source_id': 'sample_010',
            'source_site': 'sample_data',
            'magnet_link': 'magnet:?xt=urn:btih:SAMPLE023456789ABCDEF',
            'torrent_link': '',
            'size': '1.4 GiB',
            'size_bytes': 1503238553,
            'category': 'Asian',
            'censored': False,
            'country': 'TH',
            'seeders': 190,
            'leechers': 70,
            'downloads': 4200,
            'comments': 22,
            'upload_date': datetime.utcnow() - timedelta(hours=15),
            'thumbnail_url': 'https://via.placeholder.com/300x200',
            'snapshot_urls': '',
            'genres': ['Massage', 'Handjob']
        }
    ]
    
    added_count = 0
    try:
        for data in sample_data:
            result = db.add_torrent(session, data)
            if result:
                added_count += 1
        
        session.commit()
        print(f"✓ 샘플 데이터 {added_count}개 추가 완료!")
        print("\n이제 애플리케이션을 실행하여 데이터를 확인하세요:")
        print("  python main.py")
        
    except Exception as e:
        session.rollback()
        print(f"✗ 샘플 데이터 추가 실패: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    print("=" * 60)
    print("샘플 토렌트 데이터 추가")
    print("=" * 60)
    print("\n이 스크립트는 테스트용 샘플 데이터를 추가합니다.")
    print("실제 사이트 연결 없이 애플리케이션을 테스트할 수 있습니다.\n")
    
    response = input("계속하시겠습니까? (y/n): ")
    if response.lower() == 'y':
        add_sample_torrents()
    else:
        print("취소되었습니다.")

