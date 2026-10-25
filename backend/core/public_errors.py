"""Fixed public descriptions: exception messages belong only in server logs."""

DOCUMENT_PROCESSING_ERROR = (
    "Не удалось обработать документ. Проверьте настройки и доступность сервисов."
)
ARCHIVE_PROCESSING_ERROR = (
    "Не удалось обработать архив. Проверьте его содержимое и доступность сервисов."
)
PARSER_ERROR = "Не удалось прочитать документ. Проверьте формат и содержимое файла."
EMPTY_PDF_ERROR = "PDF не содержит извлекаемого текста. Возможно, это скан."


def public_operation_status(operation):
    """Also protect polling of errors recorded before the fix."""
    if operation.get("status") == "failed" or operation.get("error") is not None:
        return {**operation, "error": ARCHIVE_PROCESSING_ERROR}
    return operation
