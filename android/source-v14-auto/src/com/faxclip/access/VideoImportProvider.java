package com.faxclip.access;
import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.net.Uri;
import android.os.Bundle;
import android.os.ParcelFileDescriptor;
import android.provider.MediaStore;
import java.io.*;
import java.security.MessageDigest;

/** Authorized ADB shell writes only new, app-owned MediaStore videos. No library permission. */
public final class VideoImportProvider extends ContentProvider {
 private static final String PREFIX="content://media/external_primary/video/media/";
 private SharedPreferences records;
 @Override public boolean onCreate(){records=getContext().getSharedPreferences("imports",0);return true;}
 private boolean valid(String name){return name!=null&&name.matches("faxclip-auto-[a-f0-9]{32}\\.mp4");}
 @Override public synchronized ParcelFileDescriptor openFile(Uri request,String mode)throws FileNotFoundException{
  getContext().enforceCallingPermission("android.permission.DUMP","ADB shell only");
  String name=request.getLastPathSegment();
  if(!"w".equals(mode)||request.getPathSegments().size()!=2||!"imports".equals(request.getPathSegments().get(0))||!valid(name))throw new FileNotFoundException("Invalid import");
  if(records.contains(name))throw new FileNotFoundException("Import already exists");
  ContentValues v=new ContentValues();v.put(MediaStore.MediaColumns.DISPLAY_NAME,name);v.put(MediaStore.MediaColumns.MIME_TYPE,"video/mp4");v.put(MediaStore.MediaColumns.RELATIVE_PATH,"Movies/FaxClip/");v.put(MediaStore.MediaColumns.IS_PENDING,1);
  Uri target=getContext().getContentResolver().insert(MediaStore.Video.Media.getContentUri("external_primary"),v);
  if(target==null)throw new FileNotFoundException("MediaStore insert failed");
  if(!records.edit().putString(name,target.toString()).commit()){getContext().getContentResolver().delete(target,null,null);throw new FileNotFoundException("Record failed");}
  try{return getContext().getContentResolver().openFileDescriptor(target,"w");}catch(FileNotFoundException e){getContext().getContentResolver().delete(target,null,null);records.edit().remove(name).commit();throw e;}
 }
 @Override public synchronized Bundle call(String method,String name,Bundle extras){
  getContext().enforceCallingPermission("android.permission.DUMP","ADB shell only");
  Bundle result=new Bundle();result.putString("status","REJECTED");
  if("authorize_test_retry".equals(method)){
   if(!valid(name)||extras==null||!"RETRY_TEST".equals(extras.getString("approval")))return result;
   String sha=records.getString(name+".verified",null),account=records.getString(name+".account",null),token=extras.getString("token");
   if(!"15f5e6b4d389ecbb89b16c3208dc6af4ace336192ef8ea5a87a7db1964001393".equals(sha)||!"@redmaagi".equals(account)||!"тест".equals(records.getString(name+".caption",null))||!"PUBLISH".equals(records.getString(name+".mode",null))||!"confirmed".equals(records.getString(name+".rights",null))||token==null||!token.matches("[a-f0-9]{32}"))return result;
   if(!records.contains("publication_attempt."+account+"."+sha)||records.contains("retry_authorization."+token)||records.contains(name+".retry_token"))return result;
   if(!records.edit().putString("retry_authorization."+token,"READY").putString(name+".retry_token",token).commit())return result;
   result.putString("status","TEST_RETRY_AUTHORIZED");return result;
  }
  if("publication_status".equals(method)){
   String account=extras==null?null:extras.getString("account");
   if(name==null||!name.matches("[a-f0-9]{64}")||account==null||!account.matches("@[A-Za-z0-9._]{1,40}"))return result;
   String state=records.getString("publication_attempt."+account+"."+name,null);
   result.putString("status",state==null?"NO_ATTEMPT_RECORD":"ATTEMPT_RECORD_FOUND");
   result.putString("attempt",state==null?"NONE":state);
   result.putString("publication_confirmation","NOT_VERIFIED");
   result.putString("account",account);return result;
  }
  if("configure_job".equals(method)){
   if(!valid(name)||extras==null||records.getString(name+".verified",null)==null)return result;
   String mode=extras.getString("mode"),account=extras.getString("account"),rights=extras.getString("rights");
   if(account==null||!account.matches("@[A-Za-z0-9._]{1,40}")||!SubmissionPolicy.mode(mode,records.getString(name+".verified",null),rights))return result;
   try{
    byte[] bytes=java.util.Base64.getDecoder().decode(extras.getString("caption64"));
    if(bytes.length>16000)return result;
    String caption=java.nio.charset.StandardCharsets.UTF_8.newDecoder().onMalformedInput(java.nio.charset.CodingErrorAction.REPORT).decode(java.nio.ByteBuffer.wrap(bytes)).toString();
    if(!SubmissionPolicy.caption(caption))return result;
    if(!records.edit().putString(name+".caption",caption).putString(name+".mode",mode).putString(name+".account",account).putString(name+".rights",rights==null?"unconfirmed":rights).commit())return result;
    result.putString("status","JOB_CONFIGURED");return result;
   }catch(Exception e){return result;}
  }
  if(!"finish_import".equals(method)||!valid(name)||extras==null)return result;
  String target=records.getString(name,null);String expected=extras.getString("sha256");long expectedSize=extras.getLong("size",-1);
  if(target==null||!target.startsWith(PREFIX)||expected==null||!expected.matches("[a-f0-9]{64}")||expectedSize<=0||expectedSize>1024L*1024*1024)return result;
  Uri uri=Uri.parse(target);
  try{
   MessageDigest digest=MessageDigest.getInstance("SHA-256");long size=0;
   try(InputStream in=getContext().getContentResolver().openInputStream(uri)){
    if(in==null)throw new IOException("No stream");byte[] buffer=new byte[65536];int n;
    while((n=in.read(buffer))!=-1){size+=n;if(size>expectedSize)throw new IOException("Size exceeded");digest.update(buffer,0,n);}
   }
   StringBuilder sha=new StringBuilder();for(byte b:digest.digest())sha.append(String.format(java.util.Locale.ROOT,"%02x",b&255));
   if(size!=expectedSize||!expected.equals(sha.toString()))throw new IOException("Checksum mismatch");
   ContentValues values=new ContentValues();values.put(MediaStore.MediaColumns.IS_PENDING,0);
   if(getContext().getContentResolver().update(uri,values,null,null)!=1)throw new IOException("Finalize failed");
   if(!records.edit().putString(name+".verified",sha.toString()).commit())throw new IOException("Record verification failed");
   result.putString("status","IMPORTED");result.putString("uri",target);result.putLong("bytes",size);result.putString("sha256",sha.toString());return result;
  }catch(Exception e){result.putString("status","IMPORT_FAILED");result.putString("error",e.getClass().getSimpleName());return result;}
 }
 @Override public String getType(Uri u){return "video/mp4";}
 @Override public Cursor query(Uri u,String[] p,String s,String[] a,String o){throw new UnsupportedOperationException();}
 @Override public Uri insert(Uri u,ContentValues v){throw new UnsupportedOperationException();}
 @Override public int delete(Uri u,String s,String[] a){throw new UnsupportedOperationException();}
 @Override public int update(Uri u,ContentValues v,String s,String[] a){throw new UnsupportedOperationException();}
}
