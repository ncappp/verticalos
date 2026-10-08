package com.faxclip.access;
public final class VerificationLinkPolicy {
 private VerificationLinkPolicy(){}
 public static String extract(String text){
  if(text==null||text.length()>16384)return null;
  java.util.regex.Matcher m=java.util.regex.Pattern.compile("https://(?:www\\.tiktok\\.com/@redmaagi/video/[0-9]{10,25}|(?:vm|vt)\\.tiktok\\.com/[A-Za-z0-9]{4,40}/?)(?:\\?[^\\s<>\\\"]*)?(?=$|[\\s<>\\\"])").matcher(text);
  String found=null;int count=0;
  while(m.find()){String raw=m.group();int q=raw.indexOf('?');String clean=q<0?raw:raw.substring(0,q);if(!valid(clean))return null;found=clean;count++;}
  return count==1?found:null;
 }
 public static boolean valid(String url){return url!=null&&url.matches("https://(?:www\\.tiktok\\.com/@redmaagi/video/[0-9]{10,25}|(?:vm|vt)\\.tiktok\\.com/[A-Za-z0-9]{4,40}/?)");}
}
