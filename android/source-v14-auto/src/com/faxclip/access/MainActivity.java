package com.faxclip.access;
import android.app.Activity;
import android.os.Bundle;
import android.provider.Settings;
import android.content.Intent;
import android.graphics.Color;
import android.view.View;
import android.widget.*;
public final class MainActivity extends Activity {
 private TextView status;
 private int dp(int n){return (int)(n*getResources().getDisplayMetrics().density+.5f);}
 private TextView text(String value,int size){TextView t=new TextView(this);t.setText(value);t.setTextSize(size);t.setTextColor(Color.rgb(32,32,32));return t;}
 @Override public void onCreate(Bundle b){super.onCreate(b);
  ScrollView scroll=new ScrollView(this);LinearLayout layout=new LinearLayout(this);layout.setOrientation(LinearLayout.VERTICAL);layout.setPadding(dp(24),dp(32),dp(24),dp(24));scroll.addView(layout);
  TextView heading=text("FaxClip Screen Test v14",26);layout.addView(heading);
  TextView note=text("Интеграция с очередью FaxClip v14. Видео и точное описание берутся из задания на Mac. Поддерживаемый профиль: @redmaagi, TikTok 44.6.4. Проверка результата: отдельный Mac-скрипт открывает только опубликованные карточки профиля, проверяет автора/описание, пробует скопировать ссылку и повторно открыть пост. Не отправляет видео. Для ссылки на мгновение открывает это приложение и читает буфер обмена; произвольный текст не записывает в отчёт. Статус совпадения описания не доказывает совпадение видео по SHA-256. Диагностическое обновление: Mac может прочитать сохранённое состояние предыдущей попытки без отправки ролика. Не готовый продукт.\n\nЭтот помощник заменяет нажатия ADB, заблокированные Xiaomi. SIM-карта для самого помощника не нужна.\n\nПосле вашего разрешения он по команде Mac проверит нужный профиль TikTok и откроет «Создать» → галерея → «Видео». Если появится изученная плашка TikTok о камере и микрофоне, помощник нажмёт «Не разрешать». Если обычный клик отказа не принимается, используется жест по единственной проверенной кнопке «Не разрешать». Новый тест переносит ролик с Mac прямо в медиатеку Android и передаёт его URI в TikTok. В изученной шторке «Поделиться в TikTok» выбирает «Видео», не «Сообщение». В проверенном редакторе нажимает только «Далее» для изучения следующего экрана. На точной плашке «Продолжить редактирование публикации?» выбирает «Сохр. черновик», сохраняя прежнюю работу. Описание задаётся на Mac. В режиме подготовки публикации нет. В отдельно подтверждённом режиме публикации может принять заявленные вами права на музыку и один раз нажать «Опубликовать». Это реальная публичная отправка; результат клика требует проверки.\n\nПриложение не имеет разрешения на Интернет. Не вводит пароли, не обрабатывает коды входа. Разрешение доступа к экрану мощное — включайте его только для доверенного теста.",16);LinearLayout.LayoutParams margin=new LinearLayout.LayoutParams(-1,-2);margin.topMargin=dp(24);layout.addView(note,margin);
  status=text("",16);LinearLayout.LayoutParams sm=new LinearLayout.LayoutParams(-1,-2);sm.topMargin=dp(24);layout.addView(status,sm);
  Button button=new Button(this);button.setText("Открыть специальные возможности");button.setMinHeight(dp(48));LinearLayout.LayoutParams bm=new LinearLayout.LayoutParams(-1,-2);bm.topMargin=dp(24);layout.addView(button,bm);
  button.setOnClickListener(new View.OnClickListener(){public void onClick(View v){startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS));}});
  TextView steps=text("В системных настройках найдите «FaxClip Screen Test» и включите службу вручную. Затем запустите Mac-тест: он откроет TikTok и начнёт проверенный маршрут.\n\nНичего не нужно включать через ADB или изменять в настройках SIM. Если Android запрещает включение службы, не обходите блокировку — сообщите текст предупреждения.\n\nПосле теста отключите эту службу в специальных возможностях. Удаление приложения тоже прекращает её работу.",16);layout.addView(steps,margin);setContentView(scroll);
 }
 @Override public void onResume(){super.onResume();if(status!=null)status.setText(FaxClipAccessibility.instance==null?"Служба пока не подключена. Включение выполняется вручную в настройках Android.":"Служба подключена. Запустите новый Mac-тест автоматического открытия видео.");}
}
