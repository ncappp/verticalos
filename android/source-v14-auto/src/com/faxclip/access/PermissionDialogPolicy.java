package com.faxclip.access;
/** Narrow rule explicitly authorized by the owner: deny this TikTok camera/mic reminder only. */
public final class PermissionDialogPolicy {
 private static final String PACKAGE="com.zhiliaoapp.musically";
 private static final String MESSAGE="Приложению TikTok требуется доступ к камере и микрофону, чтобы вы могли снимать контент, экспериментировать с эффектами и т. д.";
 private static String normalize(String s){return s==null?"":s.replace('\u00a0',' ').replaceAll("\\s+"," ").trim();}
 public static boolean matchesDialog(String pkg,String message){return PACKAGE.equals(pkg)&&MESSAGE.equals(normalize(message));}
 public static boolean isDenyButton(String pkg,String text,String className,boolean clickable){return PACKAGE.equals(pkg)&&"Не разрешать".equals(normalize(text))&&"android.widget.Button".equals(className)&&clickable;}
 private PermissionDialogPolicy(){}
}
