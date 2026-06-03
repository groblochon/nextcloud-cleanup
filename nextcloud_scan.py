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

async def test_object_via_occ(urn_oid: str, sem: asyncio.Semaphore):
    # Le semaphore protège le système pour ne pas lancer des millions de process occ d'un coup
    async with sem:
        process = await asyncio.create_subprocess_exec(
            *NEXTCLOUD_OCC, "files:object:info", urn_oid,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        # IMPORTANT: il faut absolument "wait" ou "communicate" le process pour attendre sa fin !
        # Sinon process.returncode sera 'None' et Python continuera immédiatement en croyant que 
        # le fichier n'est pas cassé, ce qui laisse tourner occ en fantôme (zombie).
        stdout, stderr = await process.communicate()
        
        returncode = process.returncode
        print(f"{urn_oid} str({returncode})")
        return bool(returncode)

def delete_from_db(conn, fileid):
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

def get_all_fileids(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT fileid FROM oc_filecache ORDER BY fileid ASC")
    ids = [row[0] for row in cursor.fetchall()]
    cursor.close()
    return ids

async def process_task(fileid, conn, no_delete, sem):
    # test_object est une coroutine. Il faut l'appeler avec "await" pour récupérer son retour.
    # L'erreur de runtime venait de l'utilisation de `async with` sur des coroutines simples.
    is_broken = await test_object_via_occ(f"urn:oid:{fileid}", sem)

    if is_broken:
        if not no_delete:
            # delete_from_db est synchrone (def classique) car mysql.connector ne supporte pas l'asynchrone.
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

    # Appel d'une fonction synchrone de manière asynchrone (pas de async with ici)
    all_ids = get_all_fileids(conn)
    total = len(all_ids)
    print(f"✅ Récupéré {total} fileids")

    # Il faut un verrou (Semaphore) sinon asyncio.gather va essayer de lancer un nombre illimité 
    # de processus système occ simultanément, ce qui va complètement paralyser le serveur RAM/CPU.
    sem = asyncio.Semaphore(10)
    await asyncio.gather(*[process_task(fileid, conn, no_delete, sem) for fileid in all_ids])

    print("📁 Rescan...")
    proc1 = await asyncio.create_subprocess_exec(*NEXTCLOUD_OCC, "files:scan", "--all")
    await proc1.wait() # Indispensable d'attendre la fin !

    print("📁 Rescan app data...")
    proc2 = await asyncio.create_subprocess_exec(*NEXTCLOUD_OCC,"files:scan-app-data")
    await proc2.wait() # Indispensable d'attendre la fin !

    print("🔧 Réparation...")
    proc3 = await asyncio.create_subprocess_exec(*NEXTCLOUD_OCC,"maintenance:repair")
    await proc3.wait() # Indispensable d'attendre la fin !

asyncio.run(main())
