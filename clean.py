import os
import math
import logging
import argparse
from datetime import datetime, timedelta
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

def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description='Clean up Nextcloud leftover uploads and fix missing S3 objects.')
    parser.add_argument('--dry-run', action='store_true', help='Do not delete anything, just show what would be done.')
    parser.add_argument('--scan-all', action='store_true', help='Scan ALL files in DB and check if they exist on S3 (slow, but fixes "Failed to read object" errors).')
    args = parser.parse_args()

    # Configuration
    deletion_grace_period = int(os.getenv('DELETION_GRACE_PERIOD', 24 * 60 * 60))
    filename_pattern = os.getenv('NEXTCLOUD_FILENAME_PATTERN', 'urn:oid:%d')
    
    # DB configuration
    db_host = os.getenv('DATABASE_HOST')
    db_user = os.getenv('DATABASE_USER')
    db_pass = os.getenv('DATABASE_PASSWORD')
    db_name = os.getenv('DATABASE_NAME')

    # S3 configuration
    s3_region = os.getenv('AWS_DEFAULT_REGION')
    s3_endpoint = os.getenv('AWS_ENDPOINT')
    s3_key = os.getenv('AWS_ACCESS_KEY_ID')
    s3_secret = os.getenv('AWS_SECRET_ACCESS_KEY')
    s3_bucket = os.getenv('AWS_BUCKET')

    if not all([db_host, db_user, db_pass, db_name, s3_key, s3_secret, s3_bucket]):
        logger.error("Missing required environment variables.")
        return

    # Instantiate S3 client
    s3 = boto3.client(
        's3',
        region_name=s3_region,
        endpoint_url=s3_endpoint,
        aws_access_key_id=s3_key,
        aws_secret_access_key=s3_secret
    )

    # Instantiate DB connection
    try:
        db = mysql.connector.connect(
            host=db_host,
            user=db_user,
            password=db_pass,
            database=db_name
        )
        cursor = db.cursor(dictionary=True)
    except mysql.connector.Error as err:
        logger.error(f"Error connecting to database: {err}")
        return

    if args.scan_all:
        logger.info("Mode SCAN-ALL activé : vérification de l'intégrité de TOUS les fichiers sur S3...")
        query = """
            SELECT f.fileid, f.path, f.size, s.id AS storage
            FROM oc_filecache f
            JOIN oc_storages s ON s.numeric_id = f.storage
            WHERE s.available = 1 AND f.mimetype != 2; -- mimetype 2 = dossiers
        """
    else:
        logger.info("Mode UPLOADS activé : nettoyage des chargements temporaires abandonnés...")
        query = f"""
            SELECT 
                f.fileid, f.path, f.parent, s.id AS storage, f.size
            FROM 
                oc_filecache f
            JOIN 
                oc_storages s ON s.numeric_id = f.storage
            JOIN 
                oc_filecache p ON f.parent = p.fileid
            WHERE 
                p.parent IN (SELECT fileid FROM oc_filecache WHERE path = 'uploads')
                AND p.storage_mtime < UNIX_TIMESTAMP(NOW() - INTERVAL {deletion_grace_period} SECOND)
                AND s.available = 1;
        """

    cursor.execute(query)
    files_to_check = cursor.fetchall()
    logger.info(f"Traitement de {len(files_to_check)} fichiers...")

    parent_objects = set()
    total_size = 0
    deleted_count = 0

    for file in files_to_check:
        fileid = file['fileid']
        path = file['path']
        storage = file['storage']
        size = file['size'] or 0
        storage_filename = filename_pattern % fileid

        # Si on est en mode scan-all, on vérifie d'abord si le fichier MANQUE
        if args.scan_all:
            try:
                s3.head_object(Bucket=s3_bucket, Key=storage_filename)
                continue # Le fichier existe, on passe au suivant
            except s3.exceptions.ClientError as e:
                if e.response['Error']['Code'] == "404":
                    logger.warning(f" [!] Fichier MANQUANT sur S3 : {storage_filename} ({path})")
                else:
                    logger.error(f"Erreur API S3 pour {storage_filename}: {e}")
                    continue
            except Exception as e:
                logger.error(f"Erreur inattendue pour {storage_filename}: {e}")
                continue
        
        # Action de suppression (soit parce que c'est un upload périmé, soit parce qu'il manque sur S3)
        total_size += size
        if 'parent' in file:
            parent_objects.add(file['parent'])

        msg_prefix = "[DRY-RUN] " if args.dry_run else ""
        logger.info(f" - {msg_prefix}Suppression de l'entrée DB pour {storage_filename} / {path}")

        if not args.dry_run:
            try:
                if not args.scan_all:
                    try:
                        s3.delete_object(Bucket=s3_bucket, Key=storage_filename)
                    except Exception as e:
                        logger.error(f"Erreur suppression S3 {storage_filename}: {e}")

                delete_query = "DELETE FROM `oc_filecache` WHERE `fileid` = %s"
                cursor.execute(delete_query, (fileid,))
                db.commit()
                deleted_count += 1
            except Exception as e:
                logger.error(f"Erreur suppression DB {fileid}: {e}")

    if not args.scan_all:
        for parent_id in parent_objects:
            msg_prefix = "[DRY-RUN] " if args.dry_run else ""
            if not args.dry_run:
                cursor.execute("DELETE FROM `oc_filecache` WHERE `fileid` = %s", (parent_id,))
                db.commit()

    logger.info(f"Terminé. {deleted_count} entrées supprimées. {readable_bytes(total_size)} récupérés/nettoyés.")

    cursor.close()
    db.close()

if __name__ == "__main__":
    main()
