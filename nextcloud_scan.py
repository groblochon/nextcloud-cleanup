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

import mysql.connector
from dotenv import load_dotenv
from lib_occ import nc_occ

NC_PATH = '/var/www/nextcloud'
WEB_USER = 'nextcloud'

files_api = nc_occ.Files()
maintenance_api = nc_occ.Maintenance()

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

def test_object_via_occ(urn_oid):
    try:
        # Utilisation de lib_nc-occ via _process pour passer l'argument manquant
        output = files_api.object.get(urn_oid)
        print(output)

        if 'error' in output.lower() or 'not found' in output.lower() or \
           'does not exist' in output.lower() or 'timeout' in output.lower():

            return False

        return True

    except Exception:
        return True

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

def main():
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

        broken = []
        checked = 0
        start_time = time.time()

        for fileid in all_ids:
            checked += 1

            if checked % 50 == 0 or checked == 1:
                elapsed = time.time() - start_time
                rate = checked / (elapsed + 1)
                remaining = (total - checked) / (rate + 1)
                print(f"   [{checked}/{total}] ~{remaining:.0f}s restantes")

            if not test_object_via_occ(f"urn:oid:{fileid}"):
                broken.append(fileid)

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
            return

        print(f"❌ Trouvé {broken_count} objet(s) cassé(s) sur {total}")
        print()
        print("Objets à supprimer:")
        print()

        for fileid in broken[:20]:
            print(f"   urn:oid:{fileid}")

        if broken_count > 20:
            print(f"   ... et {broken_count - 20} autres\n")

        if no_delete:
            print("=" * 80)
            print("MODE TEST: Pas de suppression")
            print("=" * 80)
            print(f"\n✅ Scan complété")
            print(f"   {broken_count} entrée(s) SERAIENT supprimées de la DB")
            print(f"\nPour vraiment supprimer:")
            print(f"   python3 nextcloud_scan.py")
            return

        print("=" * 80)
        print("ÉTAPE 4: Confirmation")
        print("=" * 80)
        print()
        print(f"⚠️  Attention: Cela va supprimer {broken_count} entrée(s) de oc_filecache")
        print("\nTapez 'SUPPRIMER TOUT':")

        confirm = input()
        if confirm != "SUPPRIMER TOUT":
            print("\n❌ Annulé")
            return
        print()

        print("=" * 80)
        print("ÉTAPE 5: Sauvegarde")
        print("=" * 80)
        print()

        backup_file = f"/tmp/deleted_objects_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(backup_file, 'w') as f:
            for fileid in broken:
                f.write(f"urn:oid:{fileid}\n")

        print(f"💾 {backup_file}")
        print()

        print("=" * 80)
        print("ÉTAPE 6: Suppression des entrées DB")
        print("=" * 80)
        print()

        deleted = 0
        failed = 0

        for i, fileid in enumerate(broken, 1):
            if i % 10 == 0 or i == 1 or i == broken_count:
                print(f"Progression: {i}/{broken_count}")

            if delete_from_db(conn, fileid):
                deleted += 1
            else:
                failed += 1

        print()
        print(f"✅ {deleted} supprimés, {failed} échoués")
        print()

        print("=" * 80)
        print("ÉTAPE 7: Nettoyage (occ files:scan)")
        print("=" * 80)
        print()

        print("📁 Rescan...")
        files_api.scan()

        print("📁 Rescan...")
        files_api.scan_app_data()

        print("🔧 Réparation...")
        maintenance_api.repair()

        print()
        print("=" * 80)
        print("✅ SCAN ÉTENDU TERMINÉ")
        print("=" * 80)
        print()
        print(f"📊 Résumé:")
        print(f"   • Total: {total}")
        print(f"   • Cassés (S3 introuvable): {broken_count}")
        print(f"   • Supprimés de la DB: {deleted}")
        print(f"   • Échoués: {failed}")
        print(f"   • Sauvegarde: {backup_file}")

    finally:
        conn.close()

if __name__ == "__main__":
    main()
