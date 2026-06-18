import { Injectable } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';
import { Project, ProjectCreate, ProjectListResponse, ProjectUpdate } from '../models/project.model';

@Injectable({ providedIn: 'root' })
export class ProjectService {
  private readonly baseUrl = `${environment.apiBaseUrl}/projects`;

  constructor(private readonly http: HttpClient) {}

  /**
   * Возвращает постраничный список проектов.
   * @param skip  Смещение (offset), по умолчанию 0.
   * @param limit Максимальное количество записей, по умолчанию 50.
   */
  getProjects(skip = 0, limit = 50): Observable<ProjectListResponse> {
    const params = new HttpParams()
      .set('skip', skip.toString())
      .set('limit', limit.toString());
    return this.http.get<ProjectListResponse>(this.baseUrl + '/', { params });
  }

  /**
   * Возвращает проект по его UUID.
   * @param id UUID проекта.
   */
  getProject(id: string): Observable<Project> {
    return this.http.get<Project>(`${this.baseUrl}/${id}`);
  }

  /**
   * Создаёт новый проект.
   * @param project DTO с данными проекта.
   */
  createProject(project: ProjectCreate): Observable<Project> {
    return this.http.post<Project>(this.baseUrl + '/', project);
  }

  /**
   * Частично обновляет поля проекта (PATCH).
   * Передавать только изменившиеся поля.
   * @param id     UUID проекта.
   * @param update Объект с обновляемыми полями.
   */
  updateProject(id: string, update: ProjectUpdate): Observable<Project> {
    return this.http.patch<Project>(`${this.baseUrl}/${id}`, update);
  }

  deleteProject(id: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/${id}`);
  }
}
