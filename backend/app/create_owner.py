"""Create the first owner login: python -m app.create_owner "Skyler Smith" email@example.com 'password'"""

import sys

from sqlalchemy import select

from .auth import hash_password
from .db import Base, SessionLocal, engine
from .models import User

if __name__ == "__main__":
    name, email, password = sys.argv[1:4]
    assert len(password) >= 8, "password must be at least 8 characters"
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        email = email.strip().lower()
        if db.scalar(select(User).where(User.email == email)):
            sys.exit(f"{email} already exists")
        db.add(User(name=name, email=email, password_hash=hash_password(password), role="owner"))
        db.commit()
    print(f"Owner {email} created")
