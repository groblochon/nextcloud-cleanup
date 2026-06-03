#!/usr/bin/env python3
"""
SCAN ÉTENDU: Teste CHAQUE objet S3 pour vérifier son accessibilité
Utilise: occ files:object:info pour chaque urn:oid:ID
Supprime de la DB si l'objet S3 n'existe plus
"""

import os
import sys
import time
from datetime import datetime
import asyncio

import mysql.connector
from dotenv import load_dotenv
from subprocess import run

NC_PATH = '/var/www/nextcloud'
WEB_USER = 'nextcloud'
NEXTCLOUD_OCC = ["sudo", "-u", "nextcloud", "php", "--define", "apc.enable_cli=1", "/var/www/nextcloud/occ"]

# --- MONKEY PATCH LIB_OCC ---
import json

# ----------------------------

def connect_db():
    return mysql.connector.connect(
        host=os.getenv('DATABASE_HOST'),
        user=os.getenv('DATABASE_USER'),
        password=os.getenv('DATABASE_PASSWORD'),
        database=os.getenv('DATABASE_NAME')
    )

def get_all_fileids(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT fileid FROM oc_filecache ORDER BY fileid ASC")
    ids = [row[0] for row in cursor.fetchall()]
    cursor.close()
    return ids

async def test_object_via_occ(urn_oid, sem):
    async with sem:
        try:
            # create_subprocess_exec est NON-BLOQUANT. subprocess.run bloque la event loop entière !
            process = await asyncio.create_subprocess_exec(
                *NEXTCLOUD_OCC, "files:object:info", urn_oid,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            # Affichage des logs renvoyés par la commande occ
            if stdout:
                print(f"[LOG {urn_oid}] {stdout.decode().strip()}")
            if stderr:
                print(f"[ERR {urn_oid}] {stderr.decode().strip()}")

            print(f"{urn_oid} str({process.returncode})")
            return bool(process.returncode)

        except Exception as e:
            print(f"   ⚠️  Erreur test_object_via_occ {urn_oid}: {e}")
            return False

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

async def process_task(fileid, conn, no_delete, sem, stats):
    is_broken = await test_object_via_occ(f"urn:oid:{fileid}", sem)

    # L'incrémentation sous asyncio.TaskGroup (mono-thread) est sûre
    stats['checked'] += 1
    checked = stats['checked']
    total = stats['total']

    if checked % 50 == 0 or checked == 1:
        elapsed = time.time() - stats['start_time']
        rate = checked / (elapsed + 1)
        remaining = (total - checked) / (rate + 1)
        print(f"   [{checked}/{total}] {fileid} ~{remaining:.0f}s restantes")

    if is_broken:
        if not no_delete:
            stats['broken_count'] += 1
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
    print(f"Nextcloud: {NC_PATH}")
    print(f"Mode: {'TEST SEULEMENT' if no_delete else 'SUPPRESSION ACTIVE'}")
    print(f"Heure: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    conn = connect_db()

    try:
        print("=" * 80)
        print("ÉTAPE 1: Récupération de tous les fileids depuis la DB")
        print("=" * 80)
        print()

        all_ids = get_all_fileids(conn)
        total = len(all_ids)
        print(f"✅ Récupéré {total} fileids")
        print()

        if total == 0:
            print("❌ Aucun fichier trouvé")
            sys.exit(1)

        print("=" * 80)
        print("ÉTAPE 2: Test de chaque objet via occ files:object:info")
        print("=" * 80)
        print()
        print(f"Cela va prendre ~{total // 10}s (dépend de S3)...\n")

        max_workers = 10 # Ajuster selon les capacités CPU/RAM du serveur
        print(f"🚀 Lancement de {max_workers} vérifications (TaskGroup / Asyncio)...")
        sem = asyncio.Semaphore(max_workers)

        stats = {
            'checked': 0,
            'broken_count': 0,
            'start_time': time.time(),
            'total': total
        }

        # Python >3.11 : asyncio.TaskGroup()
        async with asyncio.TaskGroup() as tg:
            for fileid in all_ids:
                tg.create_task(process_task(fileid, conn, no_delete, sem, stats))

        broken_count = stats['broken_count']

        print("=" * 80)
        print("ÉTAPE 7: Nettoyage (occ files:scan)")
        print("=" * 80)
        print()

        print("📁 Rescan...")
        run(args=[*NEXTCLOUD_OCC, "files:scan", "--all"], capture_output=False, text=True)

        print("📁 Rescan...")
        run(args=[*NEXTCLOUD_OCC, "files:scan-app-data"], capture_output=False, text=True)

        print("🔧 Réparation...")
        run(args=[*NEXTCLOUD_OCC, "maintenance:repair"], capture_output=False, text=True)

        print()
        print("=" * 80)
        print("✅ SCAN ÉTENDU TERMINÉ")
        print("=" * 80)
        print()
        print(f"📊 Résumé:")
        print(f"   • Total: {total}")
        print(f"   • Cassés (S3 introuvable): {broken_count}")

    finally:
        conn.close()

if __name__ == "__main__":
    asyncio.run(main())
