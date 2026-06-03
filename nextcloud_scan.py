import asyncio
import mysql.connector
import os
import sys
import time
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
    print(f"test_object_via_occ {urn_oid}")
    # Le semaphore protège le système pour ne pas lancer des millions de process occ d'un coup
    async with sem:
        print(f"test_object_via_oc_sem {urn_oid}")
        process = await asyncio.create_subprocess_exec(
            *NEXTCLOUD_OCC, "files:object:info", urn_oid,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr =  await process.communicate()

        logs = f"LOG {urn_oid} {stdout.decode().strip()} {stderr.decode().strip()}"
        await process.wait()
        result = bool(process.returncode)
        print(f"test_object_via_oc_result {urn_oid} {result} {logs}")
        if not result:
          # do not delete on error
          if 'Failed to read object' in logs or 'timeout' in logs:
              print(f"Failed to read object {urn_oid} {logs}")
              return True
        else:
          if "does not exist" in logs:
            print(f"does not exist {urn_oid} {logs}")
            return False

        return True

def delete_from_db(conn, fileid):
    print(f"delete_from_db {fileid}")
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
    cursor.execute("SELECT fileid, path FROM oc_filecache ORDER BY fileid ASC")
    idrows = [(row[0], row[1]) for row in cursor.fetchall()]
    cursor.close()
    return idrows

async def process_task(fileid, path, conn, no_delete, sem, stats, total):
    print(f"process_task {fileid} {path}")
    is_ok = await test_object_via_occ(f"urn:oid:{fileid}", sem)

    # Statistiques et affichage des logs de progression
    stats['checked'] += 1
    checked = stats['checked']

    if checked % 100 == 0 or checked == 1:
        elapsed = time.time() - stats['start_time']
        rate = checked / (elapsed + 1)
        remaining = (total - checked) / (rate + 1)
        print(f"   [{checked}/{total}] {fileid} ~{remaining:.0f}s restantes")

    if is_ok:
        print(f"✅ {fileid}")
    else:
        stats['broken_count'] += 1
        if not no_delete:
            if delete_from_db(conn, fileid):
                print(f"⚠️ {fileid} supprimé de la DB")
            else:
                print(f"⚠️ {fileid} ECHEC suppression DB")
        else:
            print(f"👻 {fileid} fichier cassé trouvé (Omission, mode TEST)")


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
    idrows = get_all_fileids(conn)
    total = len(idrows)
    print(f"✅ Récupéré {total} fileids")

    # MAJEUR : Tu as 642 000 fichiers.
    # Si on construit une liste `[process_task(...) for ...]` avec 642 000 objets d'un coup,
    # asyncio met 2 heures à allouer la mémoire RAM, et le processus devient silencieux et gèle ("bloqué").
    # La solution est le découpage en lots (Chunks) de quelques milliers !

    sem = asyncio.Semaphore(3)
    stats = {
        'checked': 0,
        'broken_count': 0,
        'start_time': time.time()
    }

    chunk_size = 100
    for i in range(0, total, chunk_size):
        chunk = idrows[i:i+chunk_size]
        print(f"process_task {i} / {chunk[0]} / {chunk[1]} / {total}")
        # On lance 5000 vérifications maximum à la fois (dont 10 simultanément via Semaphore)
        await asyncio.gather(*[process_task(fileid, path, conn, no_delete, sem, stats, total) for (fileid, path) in chunk])

    print("📁 Rescan...")
    proc1 = await asyncio.create_subprocess_exec(*NEXTCLOUD_OCC, "files:scan", "--all")
    await proc1.wait() # Indispensable d'attendre la fin !

    print("📁 Rescan app data...")
    proc2 = await asyncio.create_subprocess_exec(*NEXTCLOUD_OCC,"files:scan-app-data")
    await proc2.wait() # Indispensable d'attendre la fin !

    print("🔧 Réparation...")
    proc3 = await asyncio.create_subprocess_exec(*NEXTCLOUD_OCC,"maintenance:repair")
    await proc3.wait() # Indispensable d'attendre la fin !

    conn.commit()
    conn.close()

asyncio.run(main())
