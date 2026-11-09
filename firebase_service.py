import json
import urllib.request
import urllib.error
from config import FIREBASE_CONFIG

FIREBASE_API_KEY = FIREBASE_CONFIG.get("apiKey", "")

FIREBASE_ERROR_MESSAGES = {
    "EMAIL_EXISTS": "An account with this email already exists in Firebase Auth.",
    "OPERATION_NOT_ALLOWED": "Email/password accounts are not enabled. Please enable 'Email/Password' under Firebase Console -> Authentication -> Sign-in method.",
    "TOO_MANY_ATTEMPTS_TRY_LATER": "We have blocked all requests from this device due to unusual activity. Try again later.",
    "EMAIL_NOT_FOUND": "No Firebase user found with this email address.",
    "INVALID_PASSWORD": "Incorrect password. Please verify your credentials.",
    "USER_DISABLED": "This user account has been disabled by an administrator.",
    "WEAK_PASSWORD": "Password should be at least 6 characters long.",
    "INVALID_EMAIL": "Please enter a valid email address.",
    "INVALID_ID_TOKEN": "The authentication token is invalid or expired."
}

def _make_firebase_request(endpoint: str, payload: dict) -> dict:
    """Helper to dispatch HTTPS requests to Firebase Identity Toolkit REST API."""
    if not FIREBASE_API_KEY:
        return {"success": False, "error": "Firebase API key is missing in configuration."}

    url = f"https://identitytoolkit.googleapis.com/v1/accounts:{endpoint}?key={FIREBASE_API_KEY}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
            result["success"] = True
            return result
    except urllib.error.HTTPError as e:
        raw_err = e.read().decode("utf-8")
        try:
            parsed = json.loads(raw_err)
            err_code = parsed.get("error", {}).get("message", "UNKNOWN_ERROR")
            # Some errors come as 'EMAIL_EXISTS : ...'
            base_code = err_code.split(" : ")[0].split(":")[0].strip()
            friendly = FIREBASE_ERROR_MESSAGES.get(base_code, f"Firebase error: {base_code}")
            return {"success": False, "error": friendly, "raw_error": err_code, "code": base_code}
        except Exception:
            return {"success": False, "error": f"Firebase HTTP error ({e.code})"}
    except urllib.error.URLError as e:
        return {"success": False, "error": f"Network error contacting Firebase: {e.reason}", "offline": True}
    except Exception as e:
        return {"success": False, "error": f"Unexpected error: {str(e)}"}

def sign_in_with_email_password(email: str, password: str) -> dict:
    """
    Authenticate user credentials using Firebase Email/Password Auth.
    Returns: dict with user details, idToken, and success boolean.
    """
    payload = {
        "email": email.strip().lower(),
        "password": password,
        "returnSecureToken": True
    }
    return _make_firebase_request("signInWithPassword", payload)

def sign_up_with_email_password(email: str, password: str, display_name: str = None) -> dict:
    """
    Register a new user account in Firebase Auth.
    """
    payload = {
        "email": email.strip().lower(),
        "password": password,
        "returnSecureToken": True
    }
    res = _make_firebase_request("signUp", payload)
    return res

def verify_id_token(id_token: str) -> dict:
    """
    Verify Firebase client ID token received from the browser.
    Returns user account information.
    """
    payload = {
        "idToken": id_token
    }
    res = _make_firebase_request("lookup", payload)
    if res.get("success") and "users" in res and len(res["users"]) > 0:
        user_info = res["users"][0]
        return {
            "success": True,
            "uid": user_info.get("localId"),
            "email": user_info.get("email"),
            "display_name": user_info.get("displayName"),
            "email_verified": user_info.get("emailVerified", False)
        }
    return res

def send_password_reset_email(email: str) -> dict:
    """
    Send password reset email to user using Firebase Auth.
    """
    payload = {
        "requestType": "PASSWORD_RESET",
        "email": email.strip().lower()
    }
    return _make_firebase_request("sendOobCode", payload)

