#!/usr/bin/env python3
#
# NZBGet SFTP Transfer Script
# Automatically transfers completed downloads from NZBGet to Windows server via SFTP.

# This script should be configured as a post-processing script in NZBGet.
# It reads NZBGet environment variables and transfers files to a remote server.
#
# Renan Mathias Fernandes

##############################################################################
### NZBGET POST-PROCESSING SCRIPT                                          ###

# SFTP Transfer Script.
#
# This script automatically transfer completed downloads to another server via SFTP when the job is done.
#
# NOTE: This script requires Python to be installed on your system.

##############################################################################
### OPTIONS                                                                ###

# Server Host where the files will be transferred to. 
#WINDOWS_SERVER_HOST="10.0.0.0"

# Server Port.
#WINDOWS_SERVER_PORT=22

# Server Username.
#WINDOWS_SERVER_USERNAME="username"

# Server Password.
#WINDOWS_SERVER_PASSWORD=""

# Destination Path on Windows Server.
#WINDOWS_DESTINATION_PATH=C:\path\to\destination

# Movies destination path (for movies category).
#MOVIES_DESTINATION_PATH=C:\Users\Administrator\Videos\Movies

# Series destination path (for TV shows/series category).
#SERIES_DESTINATION_PATH=C:\Users\Administrator\Videos\Series

# Automatically delete local files after successful transfer.
#AUTO_CLEANUP_LOCAL_FILES=yes

# Enable Pushover notifications.
#PUSHOVER_ENABLED=no

# Pushover User Key.
#PUSHOVER_USER_KEY=

# Pushover API Token.
#PUSHOVER_API_TOKEN=

### NZBGET POST-PROCESSING SCRIPT                                          ###
##############################################################################

import os
import sys
import logging
import paramiko
import stat
import requests
from pathlib import Path
from datetime import datetime

POSTPROCESS_SUCCESS = 93  # NZBGet success code for post-processing
POSTPROCESS_ERROR = 94    # NZBGet error code for post-processing failure   

def send_pushover_notification(message):
    """Send a notification via Pushover"""
    if not PUSHOVER_ENABLED:
        logger.debug("Pushover notifications are disabled")
        return
        
    if not PUSHOVER_USER_KEY or not PUSHOVER_API_TOKEN:
        logger.error("Pushover credentials are not configured. Please set PUSHOVER_USER_KEY and PUSHOVER_API_TOKEN in NZBGet settings.")
        return

    payload = {
        'token': PUSHOVER_API_TOKEN,
        'user': PUSHOVER_USER_KEY,
        'message': message
    }
    try:
        response = requests.post('https://api.pushover.net/1/messages.json', data=payload)
        response.raise_for_status()
        logger.info("Pushover Notification sent successfully.")
    except requests.RequestException as e:
        logger.error(f"Pushover Error sending notification: {e}")


def normalize_windows_path(path):
    """Convert Windows path to proper SFTP format"""
    if not path:
        return path
    
    # Remove leading slash if present before drive letter
    if path.startswith('/') and len(path) > 1 and path[2] == ':':
        path = path[1:]
    
    # Convert backslashes to forward slashes for SFTP
    path = path.replace('\\', '/')
    
    # Ensure no double slashes except after drive letter
    while '//' in path:
        path = path.replace('//', '/')
    
    return path

# Read and validate configuration from NZBGet environment variables
WINDOWS_SERVER_HOST = os.environ.get('NZBPO_WINDOWS_SERVER_HOST', '').strip()
WINDOWS_SERVER_PORT = int(os.environ.get('NZBPO_WINDOWS_SERVER_PORT', '22'))
WINDOWS_SERVER_USERNAME = os.environ.get('NZBPO_WINDOWS_SERVER_USERNAME', '').strip()   
WINDOWS_SERVER_PASSWORD = os.environ.get('NZBPO_WINDOWS_SERVER_PASSWORD', '').strip()  
WINDOWS_DESTINATION_PATH = normalize_windows_path(os.environ.get('NZBPO_WINDOWS_DESTINATION_PATH', '').strip())
MOVIES_DESTINATION_PATH = normalize_windows_path(os.environ.get('NZBPO_MOVIES_DESTINATION_PATH', '').strip())
SERIES_DESTINATION_PATH = normalize_windows_path(os.environ.get('NZBPO_SERIES_DESTINATION_PATH', '').strip())

# Cleanup configuration
AUTO_CLEANUP_LOCAL_FILES = os.environ.get('NZBPO_AUTO_CLEANUP_LOCAL_FILES', 'True').lower() in ('true', '1', 'yes', 'on')

# Pushover notification configuration
PUSHOVER_ENABLED = os.environ.get('NZBPO_PUSHOVER_ENABLED', 'no').lower() in ('true', '1', 'yes', 'on')
PUSHOVER_USER_KEY = os.environ.get('NZBPO_PUSHOVER_USER_KEY', '').strip()
PUSHOVER_API_TOKEN = os.environ.get('NZBPO_PUSHOVER_API_TOKEN', '').strip()

# Log configuration
LOG_FILE = "/tmp/nzbget_sftp_transfer.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

def get_destination_path(category):
    """Get the appropriate destination path based on NZBGet category"""
    if not category:
        category = 'default'
    
    # Convert category to lowercase for case-insensitive matching
    category_lower = category.lower()
    
    # Map categories to their specific destination paths
    if category_lower in ['movies', 'movie', 'films', 'film']:
        if MOVIES_DESTINATION_PATH:
            logger.info(f"Using Movies destination path for category '{category}': {MOVIES_DESTINATION_PATH}")
            return MOVIES_DESTINATION_PATH
    
    elif category_lower in ['series', 'tv', 'shows', 'television', 'tvshows', 'tv-shows']:
        if SERIES_DESTINATION_PATH:
            logger.info(f"Using Series destination path for category '{category}': {SERIES_DESTINATION_PATH}")
            return SERIES_DESTINATION_PATH
    
    # Fallback to default destination path
    if WINDOWS_DESTINATION_PATH:
        logger.info(f"Using default destination path for category '{category}': {WINDOWS_DESTINATION_PATH}")
        return WINDOWS_DESTINATION_PATH
    
    # If no specific path is configured, return None to trigger validation error
    logger.warning(f"No destination path configured for category '{category}'")
    return None

def validate_configuration():
    """Validate required configuration parameters"""
    errors = []
    
    if not WINDOWS_SERVER_HOST:
        errors.append("WINDOWS_SERVER_HOST is required")
    
    if not WINDOWS_SERVER_USERNAME:
        errors.append("WINDOWS_SERVER_USERNAME is required")
    
    if not WINDOWS_SERVER_PASSWORD:
        errors.append("WINDOWS_SERVER_PASSWORD is required")
    
    # Check that at least one destination path is configured
    if not WINDOWS_DESTINATION_PATH and not MOVIES_DESTINATION_PATH and not SERIES_DESTINATION_PATH:
        errors.append("At least one destination path must be configured (WINDOWS_DESTINATION_PATH, MOVIES_DESTINATION_PATH, or SERIES_DESTINATION_PATH)")
    
    if errors:
        for error in errors:
            logger.error(f"Configuration error: {error}")
        return False
    
    return True

class NZBGetSFTPTransfer:
    def __init__(self):
        self.sftp_client = None
        self.ssh_client = None
        
    def connect_sftp(self):
        """Establish SFTP connection to Windows server"""
        try:
            logger.info(f"Connecting to {WINDOWS_SERVER_HOST}:{WINDOWS_SERVER_PORT} as {WINDOWS_SERVER_USERNAME}")
            
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # Use password authentication
            logger.info("Using password authentication")
            self.ssh_client.connect(
                hostname=WINDOWS_SERVER_HOST,
                port=int(WINDOWS_SERVER_PORT),
                username=WINDOWS_SERVER_USERNAME,
                password=WINDOWS_SERVER_PASSWORD
            )
            
            self.sftp_client = self.ssh_client.open_sftp()
            logger.info(f"Successfully connected to {WINDOWS_SERVER_HOST}")
            return True
            
        except paramiko.AuthenticationException:
            logger.error("Authentication failed. Check username and password.")
            return False
        except paramiko.SSHException as e:
            logger.error(f"SSH connection error: {str(e)}")
            return False
        except OSError as e:
            logger.error(f"Connection error: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error connecting to SFTP server: {str(e)}")
            return False
    
    def create_remote_directory(self, remote_path):
        """Create directory on remote server if it doesn't exist (recursively)"""
        try:
            self.sftp_client.stat(remote_path)
            logger.info(f"Directory {remote_path} already exists")
            return
        except FileNotFoundError:
            pass
        
        # Create parent directories first
        parent_dir = os.path.dirname(remote_path)
        if parent_dir and parent_dir != remote_path:
            self.create_remote_directory(parent_dir)
        
        # Create the directory
        try:
            self.sftp_client.mkdir(remote_path)
            logger.info(f"Created directory {remote_path}")
        except Exception as e:
            # Check if it was created by another process
            try:
                self.sftp_client.stat(remote_path)
                logger.info(f"Directory {remote_path} exists (created by another process)")
            except FileNotFoundError:
                logger.error(f"Failed to create directory {remote_path}: {str(e)}")
                raise
    
    def transfer_file(self, local_file, remote_file):
        """Transfer a single file via SFTP"""
        try:
            # Ensure remote directory exists
            remote_dir = os.path.dirname(remote_file)
            self.create_remote_directory(remote_dir)
            
            # Transfer file
            self.sftp_client.put(local_file, remote_file)
            
            # Verify file was transferred
            local_size = os.path.getsize(local_file)
            remote_size = self.sftp_client.stat(remote_file).st_size
            
            if local_size == remote_size:
                logger.info(f"Successfully transferred: {local_file} -> {remote_file}")
                return True
            else:
                logger.error(f"Size mismatch: {local_file} ({local_size}) -> {remote_file} ({remote_size})")
                return False
                
        except Exception as e:
            logger.error(f"Failed to transfer {local_file}: {str(e)}")
            return False
    
    def transfer_directory(self, local_dir, remote_dir):
        """Recursively transfer a directory and all its contents"""
        success_count = 0
        total_count = 0
        
        for root, dirs, files in os.walk(local_dir):
            for file in files:
                local_file_path = os.path.join(root, file)
                
                # Calculate relative path
                relative_path = os.path.relpath(local_file_path, local_dir)
                remote_file_path = normalize_windows_path(os.path.join(remote_dir, relative_path))
                
                total_count += 1
                if self.transfer_file(local_file_path, remote_file_path):
                    success_count += 1
        
        logger.info(f"Transfer completed: {success_count}/{total_count} files transferred successfully")
        return success_count == total_count
    
    def cleanup_local_files(self, path):
        """Remove local files after successful transfer"""
        try:
            if os.path.isfile(path):
                # Single file cleanup
                file_size = os.path.getsize(path)
                os.remove(path)
                logger.info(f"Removed local file: {path} ({file_size} bytes)")
                
            elif os.path.isdir(path):
                # Directory cleanup - calculate total size first
                total_size = 0
                file_count = 0
                for root, dirs, files in os.walk(path):
                    for file in files:
                        file_path = os.path.join(root, file)
                        try:
                            total_size += os.path.getsize(file_path)
                            file_count += 1
                        except OSError:
                            pass  # Skip files we can't access
                
                # Remove the entire directory tree
                import shutil
                shutil.rmtree(path)
                logger.info(f"Removed local directory: {path} ({file_count} files, {total_size} bytes)")
            else:
                logger.warning(f"Path does not exist or is not a file/directory: {path}")
                
        except PermissionError as e:
            logger.error(f"Permission denied when cleaning up {path}: {str(e)}")
            logger.error("You may need to manually remove the files")
        except Exception as e:
            logger.error(f"Failed to cleanup {path}: {str(e)}")
            logger.error("Files were transferred successfully but local cleanup failed")
    
    def close_connection(self):
        """Close SFTP and SSH connections"""
        if self.sftp_client:
            self.sftp_client.close()
        if self.ssh_client:
            self.ssh_client.close()
        logger.info("SFTP connection closed")

def get_nzbget_variables():
    """Extract relevant NZBGet environment variables"""
    nzb_vars = {
        'name': os.environ.get('NZBPP_NZBNAME', ''),
        'directory': os.environ.get('NZBPP_DIRECTORY', ''),
        'final_directory': os.environ.get('NZBPP_FINALDIR', ''),
        'status': os.environ.get('NZBPP_STATUS', ''),
        'total_status': os.environ.get('NZBPP_TOTALSTATUS', ''),
        'category': os.environ.get('NZBPP_CATEGORY', ''),
    }
    
    logger.info(f"NZBGet variables: {nzb_vars}")
    return nzb_vars

def main():
    """Main function"""
    logger.info("Starting NZBGet SFTP Transfer Script")
    
    # Validate configuration first
    if not validate_configuration():
        logger.error("Configuration validation failed. Please check your NZBGet script settings.")
        sys.exit(POSTPROCESS_ERROR)
    
    logger.info("Configuration validation passed")
    logger.info("Variables:")
    logger.info(f"WINDOWS_SERVER_HOST: {WINDOWS_SERVER_HOST}")
    logger.info(f"WINDOWS_SERVER_PORT: {WINDOWS_SERVER_PORT}")
    logger.info(f"WINDOWS_SERVER_USERNAME: {WINDOWS_SERVER_USERNAME}")
    logger.info(f"WINDOWS_DESTINATION_PATH: {WINDOWS_DESTINATION_PATH}")
    logger.info(f"MOVIES_DESTINATION_PATH: {MOVIES_DESTINATION_PATH}")
    logger.info(f"SERIES_DESTINATION_PATH: {SERIES_DESTINATION_PATH}")
    logger.info(f"AUTO_CLEANUP_LOCAL_FILES: {AUTO_CLEANUP_LOCAL_FILES}")
    logger.info(f"PUSHOVER_ENABLED: {PUSHOVER_ENABLED}")
    logger.info(f"PUSHOVER_USER_KEY: {'***configured***' if PUSHOVER_USER_KEY else 'not set'}")
    logger.info(f"PUSHOVER_API_TOKEN: {'***configured***' if PUSHOVER_API_TOKEN else 'not set'}")
    
    # Get NZBGet variables
    nzb_vars = get_nzbget_variables()
    
    # Check if download was successful
    if nzb_vars['total_status'] != 'SUCCESS':
        logger.error(f"Download was not successful. Status: {nzb_vars['total_status']}")
        sys.exit(93)  # NZBGet error code for failed post-processing
    
    # Determine source path (use final directory if available, otherwise directory)
    source_path = nzb_vars['final_directory'] if nzb_vars['final_directory'] else nzb_vars['directory']
    
    if not source_path or not os.path.exists(source_path):
        logger.error(f"Source path does not exist: {source_path}")
        sys.exit(94)  # NZBGet error code for failed post-processing
    
    # Get category and determine destination path
    category = nzb_vars['category'] if nzb_vars['category'] else 'default'
    destination_path = get_destination_path(category)
    
    if not destination_path:
        logger.error(f"No destination path configured for category '{category}'")
        sys.exit(94)  # NZBGet error code for failed post-processing
    
    # For category-specific paths, use the path directly without adding category subfolder
    # For default path, add category as subfolder
    if destination_path == WINDOWS_DESTINATION_PATH and category != 'default':
        remote_base_path = normalize_windows_path(f"{destination_path}/{category}")
    else:
        remote_base_path = normalize_windows_path(destination_path)
    
    logger.info(f"Category: {category}")
    logger.info(f"Selected destination: {destination_path}")
    logger.info(f"Remote base path: {remote_base_path}")
    
    # Send a notification that the download has been completed and transfer is starting
    send_pushover_notification(f"SFTP Transfer starting for: {nzb_vars['name']}")
    # Initialize transfer client
    transfer_client = NZBGetSFTPTransfer()
    
    try:
        # Connect to SFTP server
        if not transfer_client.connect_sftp():
            sys.exit(94)
        
        # Transfer files
        if os.path.isfile(source_path):
            # Single file
            remote_file_path = normalize_windows_path(f"{remote_base_path}/{os.path.basename(source_path)}")
            logger.info(f"Transferring single file to: {remote_file_path}")
            success = transfer_client.transfer_file(source_path, remote_file_path)
        else:
            # Directory
            remote_dir_path = normalize_windows_path(f"{remote_base_path}/{nzb_vars['name']}")
            logger.info(f"Transferring directory to: {remote_dir_path}")
            success = transfer_client.transfer_directory(source_path, remote_dir_path)
        
        if success:
            logger.info("Transfer completed successfully")
            
            # Remove local files after successful transfer
            if AUTO_CLEANUP_LOCAL_FILES:
                logger.info(f"Cleaning up local files: {source_path}")
                transfer_client.cleanup_local_files(source_path)
            else:
                logger.info("Local file cleanup disabled - files retained locally")
            
            sys.exit(93)  # NZBGet success code
        else:
            logger.error("Transfer failed")
            sys.exit(94)  # NZBGet error code
            
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        sys.exit(94)
    finally:
        transfer_client.close_connection()

    send_pushover_notification(f"SFTP Transfer completed successfully: {nzb_vars['name']}")
    sys.exit(POSTPROCESS_SUCCESS)

if __name__ == "__main__":
    main()

