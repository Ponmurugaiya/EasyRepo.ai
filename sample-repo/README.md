# Synthetic Test Repository for EasyRepo - Codebase Intelligence Platform

This repository is a purpose-built synthetic codebase for testing Tree-sitter AST parsing, pgvector semantic embedding, and graph-based retrieval in an AI Codebase Intelligence Platform.

## Directory Structure

```
sample-repo/
├── python/
│   ├── models/
│   │   ├── base.py        -> abstract base class BaseModel with to_dict(), validate(), get_metadata()
│   │   ├── user.py         -> class UserModel(BaseModel), relative import, calls super().to_dict()
│   │   └── admin.py        -> class AdminUser(UserModel), multi-level inheritance (3 levels deep)
│   ├── interfaces/
│   │   └── repository.py   -> abstract interface Repository with abstract methods save, find_by_id, delete
│   ├── services/
│   │   ├── auth_service.py -> class AuthService(Repository) [IMPLEMENTS], has validate(token) method
│   │   └── user_service.py -> class UserService, imports & calls auth_service (cross-file CALLS)
│   ├── utils/
│   │   └── formatting.py   -> completely ORPHAN file with textually similar functions
│   └── main.py              -> entrypoint demonstrating 3-hop CALLS chain (main -> user_service -> auth_service -> base)
├── typescript/
│   ├── models/
│   │   └── user.model.ts   -> interface UserRecord + class UserModel
│   ├── interfaces/
│   │   └── repository.interface.ts -> generic interface Repository<T>
│   ├── services/
│   │   └── user.service.ts -> class UserService implements Repository<UserModel> [IMPLEMENTS]
│   └── index.ts             -> entrypoint importing and using user.service.ts with aliased import
├── docs/
│   └── ARCHITECTURE.md      -> ground truth relationship matrix & test case specification
└── README.md
```

## Running the Synthetic Code

### Python Execution
```bash
python python/main.py
```

### TypeScript Verification
```bash
# Verify syntax using tsc or npx ts-node
npx ts-node typescript/index.ts
```

## Tested Relationship Types
- **CONTAINS**: AST node nesting (modules containing classes, classes containing methods).
- **CALLS**: Direct method invocations and multi-hop call chains (`main` -> `user_service` -> `auth_service` -> `base`).
- **IMPORTS**: Relative imports (`from .base import ...`), absolute imports, and aliased imports (`import { X as Y }`).
- **INHERITS**: Single inheritance (`UserModel` -> `BaseModel`) and multi-level inheritance (`AdminUser` -> `UserModel` -> `BaseModel`).
- **IMPLEMENTS**: Abstract base class / interface implementations (`AuthService` implements `Repository`, TS `UserService` implements `Repository<UserModel>`).
- **ORPHAN NODES**: Standalone utility file (`formatting.py`) to test disconnected graph entities.
