import os
import math
import logging
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import boto3
import mysql.connector
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def readable_bytes(bytes_count):
    if bytes_count == 0:
        return "0 B"
    units = ('B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB')
    i = int(math.floor(math.log(bytes_count, 1024)))
    p = math.pow(1024, i)
    s = round(bytes_count / p, 2)
    return f"{s} {units[i]}"

def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv('DATABASE_HOST'),
        user=os.getenv('DATABASE_USER'),
        password=os.getenv('DATABASE_PASSWORD'),
        database=os.getenv('DATABASE_NAME'),
        autocommit=True # Important pour la suppression immédiate
    )

def check_and_delete(file, s3, s3_bucket, filename_pattern, dry_run, scan_all, db_write):
    fileid = file['fileid']
    path = file['path']
    size = file['size'] or 0
    storage_filename = filename_pattern % fileid
    
    # Étape 1 : Vérification (si scan-all)
    if scan_all:
        try:
            s3.head_object(Bucket=s3_bucket, Key=storage_filename)
            return None # Le fichier existe, RAS
        except s3.exceptions.ClientError as e:
            if e.response['Error']['Code'] not in ["404", "403"]:
                return f"Erreur S3 pour {storage_filename}: {e}"
        except Exception as e:
            return f"Erreur inattendue pour {storage_filename}: {e}"

    # Étape 2 : Suppression
    msg_prefix = "[DRY-RUN] " if dry_run else ""
    logger.info(f" - {msg_prefix}Nettoyage : {storage_filename} ({path})")

    if not dry_run:
        try:
            # Suppression S3 (uniquement en mode upload classique)
            if not scan_all:
                try:
                    s3.delete_object(Bucket=s3_bucket, Key=storage_filename)
                except Exception:
                    pass # On ignore si déjà supprimé sur S3

            # Suppression BDD
            cursor_write = db_write.cursor()
            cursor_write.execute("DELETE FROM `oc_filecache` WHERE `fileid` = %s", (fileid,))
            cursor_write.close()
            return ("deleted", size)
        except Exception as e:
            return f"Erreur DB pour {fileid}: {e}"
    
    return ("found", size)

def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description='Nextcloud S3 Cleanup with Workers')
    parser.add_argument('--dry-run', action='store_true', help='Simuler les suppressions')
    parser.add_argument('--scan-all', action='store_true', help='Vérifier l\'existence de TOUS les fichiers sur S3')
    parser.add_argument('--workers', type=int, default=10, help='Nombre de threads parallèles (défaut: 10)')
    args = parser.parse_args()

    # Configuration
    deletion_grace_period = int(os.getenv('DELETION_GRACE_PERIOD', 86400))
    filename_pattern = os.getenv('NEXTCLOUD_FILENAME_PATTERN', 'urn:oid:%d')
    s3_bucket = os.getenv('AWS_BUCKET')

    # Connections clients
    s3 = boto3.client('s3', 
        region_name=os.getenv('AWS_DEFAULT_REGION'),
        endpoint_url=os.getenv('AWS_ENDPOINT'),
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
    )

    db_read = get_db_connection()
    db_write = get_db_connection()
    cursor_read = db_read.cursor(dictionary=True)

    if args.scan_all:
        logger.info(f"Mode SCAN-ALL avec {args.workers} workers...")
        query = "SELECT fileid, path, size FROM oc_filecache WHERE mimetype != 2"
    else:
        logger.info(f"Mode UPLOADS avec {args.workers} workers...")
        query = f"""
            SELECT f.fileid, f.path, f.size FROM oc_filecache f
            JOIN oc_filecache p ON f.parent = p.fileid
            WHERE p.parent IN (SELECT fileid FROM oc_filecache WHERE path = 'uploads')
            AND p.storage_mtime < UNIX_TIMESTAMP(NOW() - INTERVAL {deletion_grace_period} SECOND)
        """

    cursor_read.execute(query)
    
    total_cleaned = 0
    size_recovered = 0
    
    logger.info("Démarrage du traitement...")

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(check_and_delete, file, s3, s3_bucket, filename_pattern, args.dry_run, args.scan_all, db_write): file for file in cursor_read}
        
        for future in as_completed(futures):
            result = future.result()
            if isinstance(result, tuple):
                status, size = result
                size_recovered += size
                total_cleaned += 1
            elif isinstance(result, str):
                logger.error(result)

    logger.info(f"Terminé. {total_cleaned} entrées traitées. {readable_bytes(size_recovered)} libérés.")

    cursor_read.close()
    db_read.close()
    db_write.close()

if __name__ == "__main__":
    main()
