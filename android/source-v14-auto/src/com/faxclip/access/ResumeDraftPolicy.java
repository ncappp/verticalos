package com.faxclip.access;
public final class ResumeDraftPolicy{
 public static boolean title(String pkg,String text){return "com.zhiliaoapp.musically".equals(pkg)&&text!=null&&"Продолжить редактирование публикации?".equals(text.replace('\u00a0',' ').replaceAll("\\s+"," ").trim());}
 public static boolean save(String pkg,String text){return "com.zhiliaoapp.musically".equals(pkg)&&"Сохр. черновик".equals(text);}
}
