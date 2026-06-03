import asyncio
import mysql.connector
import os
import sys
import time
import boto3
from botocore.exceptions import ClientError
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

async def test_object_via_s3(urn_oid: str, s3_client, bucket: str, sem: asyncio.Semaphore):
    # print(f"test_object_via_s3 {urn_oid}")
    # Le semaphore protège contre un trop grand nombre de requêtes simultanées à S3
    async with sem:
        # print(f"test_object_via_s3_sem {urn_oid}")
        try:
            # Utilisation de asyncio.to_thread pour ne pas bloquer la boucle d'événements
            # car boto3 est synchrone.
            await asyncio.to_thread(
                s3_client.head_object,
                Bucket=bucket,
                Key=urn_oid
            )
            return True
        except ClientError as e:
            # Code 404 signifie que l'objet est absent de S3
            if e.response['Error']['Code'] == "404":
                return False
            # En cas d'autre erreur (connexion, auth), on considère que le fichier est OK
            # pour éviter une suppression accidentelle en DB.
            print(f"   ⚠️  Erreur S3 pour {urn_oid}: {e}")
            return True
        except Exception as e:
            print(f"   ⚠️  Erreur inattendue S3 pour {urn_oid}: {e}")
            return True

def delete_from_db(conn, fileid):
    # print(f"delete_from_db {fileid}")
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

async def process_task(fileid, path, conn, no_delete, sem, stats, total, s3_client, bucket):
    # print(f"process_task {fileid} {path}")
    is_ok = await test_object_via_s3(f"urn:oid:{fileid}", s3_client, bucket, sem)

    # Statistiques et affichage des logs de progression
    stats['checked'] += 1
    checked = stats['checked']

    if checked % 100 == 0 or checked == 1:
        elapsed = time.time() - stats['start_time']
        rate = checked / (elapsed + 1)
        remaining = (total - checked) / (rate + 1)
        # print(f"   [{checked}/{total}] {fileid} ~{remaining:.0f}s restantes")

    if is_ok:
        print(f"✅ {fileid} {path}")
    else:
        stats['broken_count'] += 1
        if not no_delete:
            if delete_from_db(conn, fileid):
                print(f"⚠️ {fileid} {path} supprimé de la DB")
            else:
                print(f"⚠️ {fileid} {path} ECHEC suppression DB")
        else:
            print(f"👻 {fileid} {path} fichier cassé trouvé (Omission, mode TEST)")


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

    # Initialisation Client S3
    s3_client = boto3.client(
        's3',
        endpoint_url=os.getenv('AWS_ENDPOINT'),
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
        region_name=os.getenv('AWS_DEFAULT_REGION')
    )
    bucket = os.getenv('AWS_BUCKET')

    # On peut augmenter le sémaphore maintenant qu'on ne lance plus de sous-processus lourds
    sem = asyncio.Semaphore(20)
    stats = {
        'checked': 0,
        'broken_count': 0,
        'start_time': time.time()
    }

    chunk_size = 100 # Chunks plus gros car plus performant
    for i in range(0, total, chunk_size):
        chunk = idrows[i:i+chunk_size]
        print(f"🚀 Traitement du lot {i} à {min(i+chunk_size, total)} / {total}")
        await asyncio.gather(*[process_task(fileid, path, conn, no_delete, sem, stats, total, s3_client, bucket) for (fileid, path) in chunk])

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
