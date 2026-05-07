import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from app.auth import get_current_user, get_user_profile, get_supabase_client
from app.models.schemas import FolderResponse, FolderCreate, FolderPatch
from app.services.folder_service import (
    normalize_path,
    list_folder,
    create_folder,
    rename_folder,
    delete_folder,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/folders", tags=["folders"])


def _require_admin(user_id: str, action: str) -> None:
    """Inline admin gate (mirror of auth.py:46-51).

    Used when the admin requirement is body-conditional or row-conditional and
    therefore cannot be expressed via the standard admin dependency (which
    evaluates BEFORE body parsing). Raises HTTPException(403) if not admin.
    """
    profile = get_user_profile(user_id)
    if not profile or not profile.get("is_admin"):
        raise HTTPException(
            status_code=403,
            detail=f"Admin required for {action}",
        )


@router.get("")
async def list_folders(
    path: str = Query("/", description="Canonical folder path to list"),
    scope: str = Query("both", regex="^(user|global|both)$",
                       description="Filter scope: user | global | both (union)"),
    user_id: str = Depends(get_current_user),
):
    sb = get_supabase_client()
    try:
        norm = normalize_path(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return list_folder(norm, scope, user_id, sb)


@router.post("", response_model=FolderResponse)
async def create_folder_endpoint(
    body: FolderCreate,
    user_id: str = Depends(get_current_user),
):
    sb = get_supabase_client()
    try:
        norm = normalize_path(body.path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if body.scope == "global":
        _require_admin(user_id, "global scope")
        return create_folder(norm, "global", None, sb)

    return create_folder(norm, "user", user_id, sb)


@router.patch("/{folder_id}", response_model=FolderResponse)
async def rename_folder_endpoint(
    folder_id: str,
    body: FolderPatch,
    user_id: str = Depends(get_current_user),
):
    sb = get_supabase_client()

    # Look up the existing folder to determine its scope (admin-gate decision input).
    try:
        existing_resp = (
            sb.table("folders").select("*").eq("id", folder_id).maybe_single().execute()
        )
    except Exception:
        raise HTTPException(status_code=404, detail="Folder not found")
    if not existing_resp or not existing_resp.data:
        raise HTTPException(status_code=404, detail="Folder not found")
    folder = existing_resp.data

    # Admin gate AFTER lookup — gate decision depends on existing.scope.
    if folder["scope"] == "global":
        _require_admin(user_id, "global folder rename")

    try:
        new_path_norm = normalize_path(body.new_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        result = rename_folder(
            folder["path"], new_path_norm, folder["scope"], folder.get("user_id"), sb
        )
    except ValueError as e:
        # rename_folder raises on root-rename attempts (defense in depth).
        raise HTTPException(status_code=400, detail=str(e))

    logger.info(
        f"Folder renamed: id={folder_id} scope={folder['scope']} "
        f"old={folder['path']!r} new={new_path_norm!r} "
        f"docs_updated={result.get('documents_updated', 0)} "
        f"folders_updated={result.get('folders_updated', 0)}"
    )

    # Return merged dict — existing fields + new path + RPC counters.
    return {**folder, "path": new_path_norm, **result}


@router.delete("/{folder_id}")
async def delete_folder_endpoint(
    folder_id: str,
    user_id: str = Depends(get_current_user),
):
    sb = get_supabase_client()

    # Lookup for admin-gate decision; 404 cleanly if missing.
    try:
        existing_resp = (
            sb.table("folders").select("*").eq("id", folder_id).maybe_single().execute()
        )
    except Exception:
        raise HTTPException(status_code=404, detail="Folder not found")
    if not existing_resp or not existing_resp.data:
        raise HTTPException(status_code=404, detail="Folder not found")
    folder = existing_resp.data

    # CR-01: Ownership guard for user-scope rows. The supabase client used here is
    # service-role (bypasses RLS), so the application layer MUST enforce ownership
    # explicitly. Mirrors the pattern in routers/files.py:delete_file. Returning 404
    # (not 403) avoids leaking whether the UUID exists for another user.
    if folder["scope"] == "user" and folder.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="Folder not found")

    if folder["scope"] == "global":
        _require_admin(user_id, "global folder delete")

    # Race-free empty-check + delete via the Migration 019 RPC.
    try:
        result = delete_folder(folder_id, sb)
    except Exception as e:
        # The RPC raises 'no_data_found' SQLSTATE if the folder vanished between
        # lookup and delete (concurrent delete from another session). Map to 404.
        msg = str(e).lower()
        if "no_data_found" in msg or "not found" in msg:
            raise HTTPException(status_code=404, detail="Folder not found")
        logger.error(f"delete_folder RPC failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Delete failed: {e}")

    if not result.get("deleted"):
        return JSONResponse(status_code=409, content={
            "error": "FOLDER_NOT_EMPTY",
            "document_count": result.get("document_count", 0),
            "subfolder_count": result.get("subfolder_count", 0),
        })

    logger.info(
        f"Folder deleted: id={folder_id} scope={folder['scope']} path={folder['path']!r}"
    )
    return {"status": "deleted"}
