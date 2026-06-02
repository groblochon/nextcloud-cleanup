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

    parser = argparse.ArgumentParser(description='Clean up Nextcloud leftover uploads.')
    parser.add_argument('--dry-run', action='store_true', help='Do not delete anything, just show what would be done.')
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

    # Query to fetch files to delete
    # Note: Interval is handled slightly differently in SQL
    query = f"""
        SELECT `oc_filecache`.`fileid`, `oc_filecache`.`path`, `oc_filecache`.`parent`, `oc_storages`.`id` AS `storage`, `oc_filecache`.`size`
        FROM `oc_filecache`
        LEFT JOIN `oc_storages` ON `oc_storages`.`numeric_id` = `oc_filecache`.`storage`
        WHERE `oc_filecache`.`parent` IN (
            SELECT `fileid`
            FROM `oc_filecache`
            WHERE `parent`=(SELECT fileid FROM `oc_filecache` WHERE `path`='uploads')
            AND `storage_mtime` < UNIX_TIMESTAMP(NOW() - INTERVAL {deletion_grace_period} SECOND)
        ) AND `oc_storages`.`available` = 1;
    """

    cursor.execute(query)
    leftover_uploads = cursor.fetchall()

    logger.info(f"Found {len(leftover_uploads)} left over files.")

    parent_objects = set()
    total_size = 0

    for file in leftover_uploads:
        fileid = file['fileid']
        path = file['path']
        parent = file['parent']
        storage = file['storage']
        size = file['size'] or 0

        storage_filename = filename_pattern % fileid
        total_size += size
        parent_objects.add(parent)

        msg_prefix = "[DRY-RUN] " if args.dry_run else ""
        logger.info(f" - {msg_prefix}Deleting {storage_filename} / {path} from storage {storage} with size {readable_bytes(size)}...")

        if not args.dry_run:
            try:
                s3.delete_object(Bucket=s3_bucket, Key=storage_filename)
                
                # Delete from the DB
                delete_query = "DELETE FROM `oc_filecache` WHERE `fileid` = %s"
                cursor.execute(delete_query, (fileid,))
                db.commit()
            except Exception as e:
                logger.error(f"Error deleting {storage_filename}: {e}")

    # Delete all parent objects from the db
    for parent_id in parent_objects:
        msg_prefix = "[DRY-RUN] " if args.dry_run else ""
        logger.info(f" - {msg_prefix}Cleaning up parent object {parent_id} from DB...")
        if not args.dry_run:
            try:
                delete_query = "DELETE FROM `oc_filecache` WHERE `fileid` = %s"
                cursor.execute(delete_query, (parent_id,))
                db.commit()
            except Exception as e:
                logger.error(f"Error deleting parent {parent_id}: {e}")

    logger.info(f"Recovered {readable_bytes(total_size)} from S3 storage.")

    cursor.close()
    db.close()

if __name__ == "__main__":
    main()
