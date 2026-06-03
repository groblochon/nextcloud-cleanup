#!/usr/bin/env python3
"""
SCAN ÉTENDU: Teste CHAQUE objet S3 pour vérifier son accessibilité
Utilise: occ files:object:info pour chaque urn:oid:ID
"""

import mysql.connector
import re
import sys
import os
import subprocess
from datetime import datetime
import time
from dotenv import load_dotenv

def connect_db():
    return mysql.connector.connect(
        host=os.getenv('DATABASE_HOST'),
        user=os.getenv('DATABASE_USER'),
        password=os.getenv('DATABASE_PASSWORD'),
        database=os.getenv('DATABASE_NAME')
    )

def get_dbtable_prefix(config_path=None):
    prefix = os.getenv('DB_TABLE_PREFIX')
    if prefix:
        return prefix

    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                content = f.read()
                match = re.search(r"'dbtableprefix'\s*=>\s*'([^']+)'", content)
                if match:
                    return match.group(1)
        except:
            pass
    return 'oc_'

def get_all_files(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT fileid FROM oc_filecache")
    files = cursor.fetchall()
    cursor.close()
    return files

def test_object_via_occ(web_user, urn_oid):
    try:
        result = subprocess.run(
            [
                'sudo', '-u', web_user, 'php',
                '/var/www/nextcloud/occ',
                '--define', 'apc.enable_cli=1',
                'files:object:info',
                urn_oid
            ],
            capture_output=True,
            timeout=10,
            text=True
        )

        output = result.stdout + result.stderr

        if 'error' in output.lower() or 'not found' in output.lower() or \
           'does not exist' in output.lower():
            return False

        return True

    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return True

def delete_via_occ(web_user, urn_oid):
    try:
        result = subprocess.run(
            [
                'sudo', '-u', web_user, 'php',
                '{/var/www/nextcloud/occ',
                '--define', 'apc.enable_cli=1',
                'files:object:delete',
                urn_oid
            ],
            capture_output=True,
            timeout=10,
            text=True
        )

        return result.returncode == 0
    except:
        return False

def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv('DATABASE_HOST'),
        user=os.getenv('DATABASE_USER'),
        password=os.getenv('DATABASE_PASSWORD'),
        database=os.getenv('DATABASE_NAME')
    )

def main():
    load_dotenv()

    no_delete = '--no-delete' in sys.argv
    web_user = 'nextcloud'

    print("=" * 80)
    print("🔍 SCAN ÉTENDU - TEST DE CHAQUE OBJET S3")
    print("=" * 80)
    print(f"Mode: {'TEST SEULEMENT' if no_delete else 'SUPPRESSION ACTIVE'}")
    print(f"Heure: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    conn = get_db_connection()

    try:
        print("=" * 80)
        print("ÉTAPE 1: Récupération de tous les fichiers")
        print("=" * 80)
        print()

        all_files = get_all_files(conn)
        total = len(all_files)

        print(f"✅ Récupéré {total} fichiers")
        print()

        if total == 0:
            print("❌ Aucun fichier trouvé")
            sys.exit(1)

        print("=" * 80)
        print("ÉTAPE 2: Test de chaque objet via occ")
        print("=" * 80)
        print()
        print(f"Cela va prendre ~{total // 10}s (dépend de S3)...\n")

        broken = []
        checked = 0
        start_time = time.time()

        for obj_id in all_files:
            checked += 1

            if checked % 50 == 0 or checked == 1:
                elapsed = time.time() - start_time
                rate = checked / (elapsed + 1)
                remaining = (total - checked) / (rate + 1)
                print(f"   [{checked}/{total}] ~{remaining:.0f}s restantes")

            urn_oid = f"urn:oid:{obj_id}"

            if not test_object_via_occ(web_user, urn_oid):
                broken.append((obj_id))

        elapsed = time.time() - start_time
        print()
        print(f"✅ Scan complété en {elapsed:.0f}s")
        print()

        print("=" * 80)
        print("ÉTAPE 3: Résultats")
        print("=" * 80)
        print()

        broken_count = len(broken)

        if broken_count == 0:
            print("🎉 Aucun objet cassé trouvé!")
            print(f"Tous les {total} fichiers sont accessibles.")
            conn.close()
            return

        print(f"❌ Trouvé {broken_count} objet(s) cassé(s) sur {total}")
        print()
        print("Objets à supprimer:")
        print()

        for obj_id in broken[:20]:
            print(f"   urn:oid:{obj_id}")

        if broken_count > 20:
            print(f"   ... et {broken_count - 20} autres\n")

        if no_delete:
            print("=" * 80)
            print("MODE TEST: Pas de suppression")
            print("=" * 80)
            print(f"\n✅ Scan complété")
            print(f"   {broken_count} objet(s) SERAIENT supprimés")
            print(f"\nPour vraiment les supprimer:")
            conn.close()
            return

        print("=" * 80)
        print("ÉTAPE 4: Confirmation")
        print("=" * 80)
        print()
        print(f"⚠️  Attention: Cela va supprimer {broken_count} objet(s)")
        print("\nTapez 'SUPPRIMER TOUT':")

        confirm = input()

        if confirm != "SUPPRIMER TOUT":
            print("\n❌ Annulé")
            conn.close()
            return

        print()

        print("=" * 80)
        print("ÉTAPE 5: Sauvegarde")
        print("=" * 80)
        print()

        backup_file = f"/tmp/deleted_objects_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(backup_file, 'w') as f:
            for obj_id in broken:
                f.write(f"urn:oid:{obj_id}")

        print(f"💾 {backup_file}")
        print()

        print("=" * 80)
        print("ÉTAPE 6: Suppression")
        print("=" * 80)
        print()

        deleted = 0
        failed = 0

        for i, obj_id in enumerate(broken, 1):
            urn_oid = f"urn:oid:{obj_id}"

            if i % 10 == 0 or i == 1 or i == broken_count:
                print(f"Progression: {i}/{broken_count}")

            if delete_via_occ(web_user, urn_oid):
                deleted += 1
            else:
                failed += 1

        print()
        print(f"✅ {deleted} supprimés, {failed} échoués")
        print()

        print("=" * 80)
        print("ÉTAPE 7: Nettoyage")
        print("=" * 80)
        print()

        print("🔒 Mode maintenance...")
        os.system(f"sudo -u {web_user} php /var/www/nextcloud/occ maintenance:mode --on >/dev/null 2>&1")

        print("📁 Rescan...")
        os.system(f"sudo -u {web_user} php /var/www/nextcloud/occ files:scan --all >/dev/null 2>&1")

        print("🔧 Réparation...")
        os.system(f"sudo -u {web_user} php /var/www/nextcloud/occ maintenance:repair >/dev/null 2>&1")

        print("🔓 Mode normal...")
        os.system(f"sudo -u {web_user} php /var/www/nextcloud/occ maintenance:mode --off >/dev/null 2>&1")

        print()
        print("=" * 80)
        print("✅ SCAN ÉTENDU TERMINÉ")
        print("=" * 80)
        print()
        print(f"📊 Résumé:")
        print(f"   • Total: {total}")
        print(f"   • Cassés: {broken_count}")
        print(f"   • Supprimés: {deleted}")
        print(f"   • Échoués: {failed}")
        print(f"   • Sauvegarde: {backup_file}")

    finally:
        conn.close()

if __name__ == "__main__":
    main()
