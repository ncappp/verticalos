package com.faxclip.access;
import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.content.ClipboardManager;
import android.content.ClipData;
import android.content.SharedPreferences;
import java.security.MessageDigest;
import java.util.regex.Pattern;
import java.util.regex.Matcher;
public final class VerificationClipboardActivity extends Activity {
 private static String digest(String s)throws Exception{byte[] b=MessageDigest.getInstance("SHA-256").digest(s.getBytes("UTF-8"));StringBuilder o=new StringBuilder();for(byte x:b)o.append(String.format("%02x",x&255));return o.toString();}
 @Override public void onCreate(Bundle b){super.onCreate(b);android.widget.TextView t=new android.widget.TextView(this);t.setText("FaxClip: проверка ссылки, без публикации.");setContentView(t);}
 private final Handler reader=new Handler();private int attempts=0;
 @Override public void onResume(){super.onResume();attempts=0;reader.removeCallbacksAndMessages(null);reader.postDelayed(new Runnable(){public void run(){
  if(!hasWindowFocus()&&++attempts<10){reader.postDelayed(this,200);return;}
  read();finish();
 }},600);}
 @Override public void onPause(){super.onPause();reader.removeCallbacksAndMessages(null);}
 private void read(){
  SharedPreferences p=getSharedPreferences("verification",0);
  String mode=getIntent().getStringExtra("mode"),token=getIntent().getStringExtra("token");
  if(token==null||!token.matches("[a-f0-9]{32}"))return;
  try{
   if(!hasWindowFocus()){p.edit().putString("result","CLIPBOARD_NOT_FOCUSED").remove("url").commit();return;}
   ClipboardManager c=(ClipboardManager)getSystemService(CLIPBOARD_SERVICE);
   ClipData clip=c.getPrimaryClip();String text="";
   if(clip!=null&&clip.getItemCount()==1&&clip.getItemAt(0).getText()!=null)text=clip.getItemAt(0).getText().toString();
   long timestamp=clip==null||clip.getDescription()==null?0:clip.getDescription().getTimestamp();
   if(text.length()>16384)text="OVERSIZE";
   if("baseline".equals(mode)){p.edit().putString("token",token).putString("baseline",digest(text)).putLong("baseline_time",timestamp).putString("result","BASELINE_CAPTURED").remove("url").commit();return;}
   if(!"read".equals(mode)||!token.equals(p.getString("token",null)))return;
   FaxClipAccessibility service=FaxClipAccessibility.instance;
   if(service==null||!"COPY_LINK_ACTION_ACCEPTED".equals(service.verificationResult())){p.edit().putString("result","COPY_LINK_NOT_CONFIRMED").commit();return;}
   if(!VerificationFreshnessPolicy.fresh(!digest(text).equals(p.getString("baseline","")),p.getLong("baseline_time",0),timestamp)){p.edit().putString("result","NO_FRESH_LINK").commit();return;}
   String url=VerificationLinkPolicy.extract(text);
   if(url==null){p.edit().putString("result","NO_VALID_TIKTOK_LINK").commit();return;}
   p.edit().putString("result","FRESH_TIKTOK_LINK_CAPTURED").putString("url",url).commit();
  }catch(Exception e){p.edit().putString("result","CLIPBOARD_READ_FAILED").commit();}
 }
}
