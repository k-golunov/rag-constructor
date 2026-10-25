import { AbstractControl, ValidationErrors } from '@angular/forms';

export function chunkOverlapValidator(group: AbstractControl): ValidationErrors | null {
  return Number(group.get('chunk_overlap')?.value) >= Number(group.get('chunk_size')?.value)
    ? { chunkOverlap: true } : null;
}

export function apiErrorMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(error => error.msg).join('; ');
  return (detail as { message?: string } | null)?.message ?? fallback;
}
