import hashlib


def user_namespace(auth0_subject):
    if not isinstance(auth0_subject, str) or not auth0_subject.strip():
        raise ValueError("An authenticated account subject is required.")
    digest = hashlib.sha256(auth0_subject.encode("utf-8")).hexdigest()
    return f"account-{digest[:40]}"


def user_key(auth0_subject):
    if not isinstance(auth0_subject, str) or not auth0_subject.strip():
        raise ValueError("An authenticated account subject is required.")
    return hashlib.sha256(auth0_subject.encode("utf-8")).hexdigest()