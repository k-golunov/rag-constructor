import { Routes } from '@angular/router';

export const routes: Routes = [
  {
    path: '',
    loadComponent: () =>
      import('./pages/home/home.component').then((m) => m.HomeComponent),
    title: 'RAG Constructor',
  },
  {
    path: 'projects',
    children: [
      {
        path: '',
        loadComponent: () =>
          import('./pages/projects/list/project-list.component').then(
            (m) => m.ProjectListComponent,
          ),
        title: 'Мои проекты — RAG Constructor',
      },
      {
        path: 'new',
        loadComponent: () =>
          import('./pages/projects/create/project-create.component').then(
            (m) => m.ProjectCreateComponent,
          ),
        title: 'Создать проект — RAG Constructor',
      },
      {
        // Placeholder for project detail — replace with real component later
        path: ':id',
        loadComponent: () =>
          import('./pages/projects/list/project-list.component').then(
            (m) => m.ProjectListComponent,
          ),
        title: 'Проект — RAG Constructor',
      },
    ],
  },
];
