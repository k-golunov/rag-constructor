import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';

interface Feature {
  icon: string;
  title: string;
  description: string;
}

@Component({
  selector: 'app-home',
  standalone: true,
  imports: [RouterLink],
  templateUrl: './home.component.html',
  styleUrl: './home.component.css',
})
export class HomeComponent {
  features: Feature[] = [
    {
      icon: 'upload_file',
      title: 'Загрузка любых документов',
      description:
        'Поддержка PDF, DOCX, TXT и других форматов. Загружайте документы любого размера — система автоматически обработает и проиндексирует содержимое.',
    },
    {
      icon: 'tune',
      title: 'Настройка без кода',
      description:
        'Интуитивный интерфейс позволяет выбрать модель, настроить чанкинг и эмбеддинги без единой строчки программирования.',
    },
    {
      icon: 'lock',
      title: 'Приватность данных',
      description:
        'Ваши документы хранятся в изолированной среде. Полный контроль над данными: никакой передачи третьим сторонам без вашего согласия.',
    },
  ];
}
