package com.faxclip.access;
public final class PublicVisibilityPolicy {
 public static final String TEXT="Эту публикацию могут просматривать все";
 public static boolean text(String s){return s!=null&&TEXT.equals(s.replace('\u00a0',' ').replaceAll("\\s+"," ").trim());}
 public static boolean row(String pkg,String id,String role,String label,String parentId,String parentRole,String parentDescription){
  return "com.zhiliaoapp.musically".equals(pkg)&&"com.zhiliaoapp.musically:id/xfi".equals(id)&&"android.widget.TextView".equals(role)&&text(label)&&"com.zhiliaoapp.musically:id/db8".equals(parentId)&&"android.widget.Button".equals(parentRole)&&text(parentDescription);
 }
}
