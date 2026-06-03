import asyncio
import mysql.connector
import os
import sys
from dotenv import load_dotenv
from datetime import datetime

WEB_USER = 'nextcloud'
NEXTCLOUD_OCC = ["sudo", "-u", "nextcloud", "php", "--define", "apc.enable_cli=1", "/var/www/nextcloud/occ"]

def connect_db():
    return mysql.connector.connect(
        host=os.getenv('DATABASE_HOST'),
        user=os.getenv('DATABASE_USER'),
        password=os.getenv('DATABASE_PASSWORD'),
        database=os.getenv('DATABASE_NAME')
    )

async def test_object_via_occ(urn_oid :str):
    process = await asyncio.create_subprocess_exec(*NEXTCLOUD_OCC, "files:object:info", urn_oid)

    print(f"{urn_oid} str({process.returncode})")
    return bool(process.returncode)


async def delete_from_db(conn, fileid):
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM oc_filecache WHERE fileid = %s", (fileid,))
        conn.commit()
        deleted = cursor.rowcount > 0
        cursor.close()
        return deleted
    except Exception as e:
        print(f"   ⚠️  Erreur DELETE DB {fileid}: {e}")
        return False

async def get_all_fileids(conn):
    cursor = conn.cursor()
    await cursor.execute("SELECT fileid FROM oc_filecache ORDER BY fileid ASC")
    ids = [row[0] for row in cursor.fetchall()]
    cursor.close()
    return ids

async def process_task(fileid, conn, no_delete):

    async with test_object_via_occ(f"urn:oid:{fileid}") as is_broken:

      if is_broken:
          if not no_delete:
              if delete_from_db(conn, fileid):
                  print(f"{fileid} deleted in db")
              else:
                  print(f"{fileid} NOT deleted in db")
          else:
            print(f"{fileid} NOT deleted in db")


async def main():

  load_dotenv()
  no_delete = '--no-delete' in sys.argv

  print("=" * 80)
  print("🔍 SCAN ÉTENDU - TEST DE CHAQUE OBJET S3")
  print("=" * 80)
  print(f"Mode: {'TEST SEULEMENT' if no_delete else 'SUPPRESSION ACTIVE'}")
  print(f"Heure: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

  conn = connect_db()

  async with get_all_fileids(conn) as all_ids:
    total = len(all_ids)
    print(f"✅ Récupéré {total} fileids")

    await asyncio.gather(*[process_task(fileid, conn, no_delete) for fileid in all_ids])

    print("📁 Rescan...")
    await asyncio.create_subprocess_exec(*NEXTCLOUD_OCC, "files:scan", "--all")

    print("📁 Rescan app data...")
    await asyncio.create_subprocess_exec(*NEXTCLOUD_OCC,"files:scan-app-data")

    print("🔧 Réparation...")
    await asyncio.create_subprocess_exec(*NEXTCLOUD_OCC,"maintenance:repair")


asyncio.run(main())
