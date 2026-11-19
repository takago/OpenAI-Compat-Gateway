"""Generate a password hash for ADMIN_PASSWORD_HASH without printing the password."""
import getpass
from management import password_hash

password = getpass.getpass('New administrator password: ')
confirm = getpass.getpass('Confirm password: ')
if len(password) < 12 or password != confirm:
    raise SystemExit('Password must contain at least 12 characters and match the confirmation')
print('ADMIN_PASSWORD_HASH=' + password_hash(password))
