package com.faxclip.access;
public final class SubmissionPolicy{
 public static final String TEST_HASH="d16281beea370ae69961ce70abaa3a1f137895f8be5ee0e69159ed3aa319c3ff";
 public static boolean name(String n){return n!=null&&n.matches("faxclip-auto-[a-f0-9]{32}\\.mp4");}
 public static boolean caption(String s){
  if(s==null||s.trim().isEmpty()||s.codePointCount(0,s.length())>2200)return false;
  for(int i=0;i<s.length();i++){char c=s.charAt(i);if((c<32&&c!='\n'&&c!='\t')||c==127)return false;}return true;
 }
 public static boolean mode(String mode,String sha,String rights){return "PREPARE".equals(mode)||("PUBLISH".equals(mode)&&"confirmed".equals(rights)&&sha!=null&&!TEST_HASH.equals(sha)&&!"51a0f743fba62385ae4f8887615f3e8d7c21ef4a19cf1e21c6e079bec2b66391".equals(sha));}
}
