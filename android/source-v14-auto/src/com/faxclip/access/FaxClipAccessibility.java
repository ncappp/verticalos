package com.faxclip.access;
import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.graphics.Path;
import android.graphics.Rect;
import android.view.accessibility.AccessibilityWindowInfo;
import android.os.Handler;
import android.os.Bundle;
import android.content.SharedPreferences;
import android.os.Looper;
import android.os.SystemClock;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import java.util.List;
public final class FaxClipAccessibility extends AccessibilityService {
 public static volatile FaxClipAccessibility instance;
 private boolean busy=false;
 private String confirmedAccount="";
 private String activeJob="";
 private volatile String progress="IDLE";
 private volatile String diagnostics="NOT_STARTED";
 public String routeDiagnostics(){return diagnostics;}
 private final Handler handler=new Handler(Looper.getMainLooper());
 public interface Completion{void done(String result);}
 @Override protected void onServiceConnected(){super.onServiceConnected();instance=this;}
 @Override public void onAccessibilityEvent(AccessibilityEvent event){}
 @Override public void onInterrupt(){handler.removeCallbacksAndMessages(null);busy=false;progress="INTERRUPTED";}
 @Override public void onDestroy(){handler.removeCallbacksAndMessages(null);busy=false;progress="SERVICE_DESTROYED";if(instance==this)instance=null;super.onDestroy();}
 private static final String TIKTOK="com.zhiliaoapp.musically";
 private boolean tikTok(AccessibilityNodeInfo n){return n!=null&&TIKTOK.contentEquals(n.getPackageName()==null?"":n.getPackageName());}
 private AccessibilityNodeInfo root(){
  AccessibilityNodeInfo active=getRootInActiveWindow();
  if(active!=null)active.refresh();
  // Never act on a background app when another package is foreground.
  if(!tikTok(active))return null;
  AccessibilityNodeInfo selected=active;int layer=Integer.MIN_VALUE;
  for(AccessibilityWindowInfo w:getWindows()){
   // TikTok may expose an app-owned floating banner as a non-application window.
   if(w.getType()==AccessibilityWindowInfo.TYPE_INPUT_METHOD)continue;
   AccessibilityNodeInfo candidate=w.getRoot();
   if(candidate!=null)candidate.refresh();
   if(tikTok(candidate)&&w.getLayer()>layer){selected=candidate;layer=w.getLayer();}
  }
  return selected;
 }
 // Verification is a separate, bounded read/navigation route. No submission calls.
 private String verification="IDLE";
 private String verificationCaption=null;
 public String verificationJob(String name){
  if(busy)return "BUSY";
  if(!SubmissionPolicy.name(name))return "INVALID_JOB";
  SharedPreferences records=jobRecords();
  String caption=records.getString(name+".caption",null);
  if(records.getString(name+".verified",null)==null||!"@redmaagi".equals(records.getString(name+".account",null))||!SubmissionPolicy.caption(caption))return "VERIFICATION_JOB_NOT_CONFIRMED";
  verificationCaption=caption;verification="IDLE";return "VERIFICATION_JOB_BOUND";
 }
 public String verificationResult(){return verification;}
 private boolean exact(AccessibilityNodeInfo n,String text){return n!=null&&n.getText()!=null&&text.contentEquals(n.getText());}
 public String verificationCandidate(int index){
  if(busy)return "BUSY";
  if(verificationCaption==null)return "VERIFICATION_JOB_NOT_BOUND";
  verification="NOT_VERIFIED";
  if(index<0||index>2)return "INVALID_CANDIDATE_INDEX";
  AccessibilityNodeInfo r=root();if(r==null)return "TIKTOK_NOT_FOREGROUND";
  if(resumeDraftRoot()!=null)return "PREVIOUS_WORK_OVERLAY_NO_ACTION";
  if(!exact(unique(r,TIKTOK+":id/rhk"),"@redmaagi"))return "PROFILE_ACCOUNT_NOT_CONFIRMED";
  int tabs=0;
  for(AccessibilityNodeInfo n:r.findAccessibilityNodeInfosByViewId(TIKTOK+":id/jid")){
   if(n.refresh()&&n.isVisibleToUser()&&n.isSelected()&&"Публикации".contentEquals(n.getContentDescription()==null?"":n.getContentDescription()))tabs++;
  }
  if(tabs!=1)return "PUBLICATIONS_TAB_NOT_CONFIRMED";
  java.util.ArrayList<AccessibilityNodeInfo> tiles=new java.util.ArrayList<>();
  for(AccessibilityNodeInfo n:r.findAccessibilityNodeInfosByViewId(TIKTOK+":id/eh7")){
   if(!n.refresh()||!n.isVisibleToUser()||!n.isEnabled()||!n.isClickable()||!tikTok(n)||!"android.widget.FrameLayout".contentEquals(n.getClassName()==null?"":n.getClassName()))continue;
   if(!n.findAccessibilityNodeInfosByText("Черновики").isEmpty())continue;
   tiles.add(n);
  }
  // Only known published-video containers. Draft cover has a different ID and is never clicked.
  if(index>=tiles.size())return "NO_MORE_VISIBLE_PUBLIC_TILES";
  if(!click(tiles.get(index)))return "CANDIDATE_CLICK_REJECTED";
  verification="PROFILE_CANDIDATE_OPENED";return verification;
 }
 public String verificationInspect(boolean reopened){
  if(busy)return "BUSY";
  if(verificationCaption==null)return "VERIFICATION_JOB_NOT_BOUND";
  AccessibilityNodeInfo r=root();if(r==null)return "TIKTOK_NOT_FOREGROUND";
  if(captionField(r)!=null||unique(r,TIKTOK+":id/rsn")!=null)return "EDITOR_NOT_A_PUBLISHED_POST";
  if(!exact(unique(r,TIKTOK+":id/title"),"Redmaagi")||!exact(unique(r,TIKTOK+":id/desc"),verificationCaption))return "POST_AUTHOR_OR_CAPTION_NOT_MATCHED";
  if(reopened){
   SharedPreferences evidence=getSharedPreferences("verification",0);
   if(!"FRESH_TIKTOK_LINK_CAPTURED".equals(evidence.getString("result",""))||!VerificationLinkPolicy.valid(evidence.getString("url",null)))return "FRESH_LINK_NOT_CONFIRMED";
   if(!"COPY_LINK_ACTION_ACCEPTED".equals(verification)&&!"MATCHING_POST_REOPENED_BY_URL".equals(verification))return "LINK_ORIGIN_NOT_CONFIRMED";
   verification="MATCHING_POST_REOPENED_BY_URL";return verification;
  }
  if(!"PROFILE_CANDIDATE_OPENED".equals(verification))return "PROFILE_ORIGIN_NOT_CONFIRMED";
  verification="PROFILE_MATCHING_CAPTION_FOUND";return verification;
 }
 public String verificationShare(){
  if(busy)return "BUSY";
  if(!"PROFILE_MATCHING_CAPTION_FOUND".equals(verification))return "PROFILE_MATCH_NOT_CONFIRMED";
  AccessibilityNodeInfo r=root();if(r==null)return "TIKTOK_NOT_FOREGROUND";
  if(!exact(unique(r,TIKTOK+":id/title"),"Redmaagi")||!exact(unique(r,TIKTOK+":id/desc"),verificationCaption))return "POST_CHANGED_NO_ACTION";
  AccessibilityNodeInfo target=null;
  for(AccessibilityNodeInfo n:r.findAccessibilityNodeInfosByViewId(TIKTOK+":id/fj4")){
   if(!n.refresh()||!n.isVisibleToUser()||!n.isEnabled()||!n.isClickable()||!"android.widget.Button".contentEquals(n.getClassName()==null?"":n.getClassName()))continue;
   String d=n.getContentDescription()==null?"":n.getContentDescription().toString();
   if(!d.startsWith("Поделиться видео. Уже поделились:"))continue;
   if(target!=null)return "AMBIGUOUS_SHARE_NO_ACTION";target=n;
  }
  if(!click(target))return "SHARE_NOT_AVAILABLE";
  verification="VERIFICATION_SHARE_OPENED";return verification;
 }
 public String verificationCopyLink(){
  if(busy)return "BUSY";
  if(!"VERIFICATION_SHARE_OPENED".equals(verification))return "SHARE_ORIGIN_NOT_CONFIRMED";
  AccessibilityNodeInfo r=root();if(r==null)return "TIKTOK_NOT_FOREGROUND";
  AccessibilityNodeInfo target=null;
  for(String label:new String[]{"Копировать ссылку","Скопировать ссылку","Ссылка"}){
   for(AccessibilityNodeInfo n:r.findAccessibilityNodeInfosByText(label)){
    if(!n.refresh()||!n.isVisibleToUser()||!n.isEnabled()||!exact(n,label)||!tikTok(n))continue;
    AccessibilityNodeInfo action=n;
    for(int i=0;i<2&&action!=null&&!action.isClickable();i++)action=action.getParent();
    if(action==null||!action.refresh()||!action.isVisibleToUser()||!action.isEnabled()||!action.isClickable()||!tikTok(action))continue;
    if(target!=null&&!target.equals(action))return "AMBIGUOUS_COPY_LINK_NO_ACTION";target=action;
   }
  }
  if(!click(target))return "COPY_LINK_NOT_AVAILABLE";
  verification="COPY_LINK_ACTION_ACCEPTED";return verification;
 }
 public String windowState(){
  AccessibilityNodeInfo active=getRootInActiveWindow();
  StringBuilder out=new StringBuilder("active_pkg=").append(active==null?"NULL":active.getPackageName());
  for(AccessibilityWindowInfo w:getWindows()){
   AccessibilityNodeInfo r=w.getRoot();out.append(";window_type=").append(w.getType()).append(",layer=").append(w.getLayer()).append(",pkg=").append(r==null?"NULL":r.getPackageName());
  }
  return out.toString();
 }
 private AccessibilityNodeInfo resumeDraftRoot(){
  AccessibilityNodeInfo active=getRootInActiveWindow();if(!tikTok(active))return null;
  AccessibilityNodeInfo found=null;
  java.util.ArrayList<AccessibilityNodeInfo> roots=new java.util.ArrayList<>();roots.add(active);
  for(AccessibilityWindowInfo w:getWindows()){AccessibilityNodeInfo r=w.getRoot();if(tikTok(r))roots.add(r);}
  for(AccessibilityNodeInfo r:roots){
   for(AccessibilityNodeInfo n:r.findAccessibilityNodeInfosByText("Продолжить редактирование")){
    if(n.refresh()&&n.isVisibleToUser()&&ResumeDraftPolicy.title(n.getPackageName()==null?null:n.getPackageName().toString(),n.getText()==null?null:n.getText().toString())){found=r;break;}
   }
   if(found!=null)break;
  }
  return found;
 }
 private AccessibilityNodeInfo resumeSaveTarget(AccessibilityNodeInfo r){
  if(r==null)return null;AccessibilityNodeInfo target=null;
  for(AccessibilityNodeInfo n:r.findAccessibilityNodeInfosByText("Сохр. черновик")){
   if(!n.refresh()||!n.isVisibleToUser()||!n.isEnabled()||!ResumeDraftPolicy.save(n.getPackageName()==null?null:n.getPackageName().toString(),n.getText()==null?null:n.getText().toString()))continue;
   AccessibilityNodeInfo action=n;
   // Text labels may be children of a button; never climb beyond two ancestors.
   for(int i=0;i<2&&!action.isClickable();i++){action=action.getParent();if(action==null)break;}
   if(action==null||!action.refresh()||!action.isVisibleToUser()||!action.isEnabled()||!action.isClickable()||!tikTok(action))continue;
   if(!action.findAccessibilityNodeInfosByText("Изменить").isEmpty())continue;
   if(target!=null)return null;target=action;
  }
  return target;
 }
 private AccessibilityNodeInfo unique(AccessibilityNodeInfo root,String id){
  List<AccessibilityNodeInfo> nodes=root.findAccessibilityNodeInfosByViewId(id);AccessibilityNodeInfo found=null;
  for(AccessibilityNodeInfo node:nodes){if(!node.refresh()||!node.isVisibleToUser()||!node.isEnabled())continue;if(found!=null)return null;found=node;}
  return found;
 }
 private boolean click(AccessibilityNodeInfo n){return n!=null&&n.refresh()&&n.isVisibleToUser()&&n.isEnabled()&&n.isClickable()&&n.performAction(AccessibilityNodeInfo.ACTION_CLICK);}
 private boolean isCameraMicReminder(AccessibilityNodeInfo root){
  AccessibilityNodeInfo message=unique(root,"com.zhiliaoapp.musically:id/en6");
  return message!=null&&PermissionDialogPolicy.matchesDialog("com.zhiliaoapp.musically",message.getText()==null?null:message.getText().toString());
 }
 private AccessibilityNodeInfo denyTarget(AccessibilityNodeInfo root){
  if(root==null||!isCameraMicReminder(root))return null;
  AccessibilityNodeInfo target=null;
  for(AccessibilityNodeInfo n:root.findAccessibilityNodeInfosByText("Не разрешать")){
   if(!n.isVisibleToUser()||!n.isEnabled())continue;
   if(PermissionDialogPolicy.isDenyButton(n.getPackageName()==null?null:n.getPackageName().toString(),n.getText()==null?null:n.getText().toString(),n.getClassName()==null?null:n.getClassName().toString(),n.isClickable())){
    if(target!=null)return null;target=n;
   }
  }
  return target;
 }
 private boolean denyGesture(){
  // Re-fetch and verify exact dialog and unique button immediately before gesture.
  AccessibilityNodeInfo fresh=root();AccessibilityNodeInfo target=denyTarget(fresh);
  if(target==null||!target.refresh()||!target.isVisibleToUser()||!target.isEnabled())return false;
  Rect bounds=new Rect();target.getBoundsInScreen(bounds);
  Rect window=new Rect();fresh.getBoundsInScreen(window);
  if(bounds.isEmpty()||!window.contains(bounds))return false;
  Path path=new Path();path.moveTo(bounds.exactCenterX(),bounds.exactCenterY());
  GestureDescription gesture=new GestureDescription.Builder().addStroke(new GestureDescription.StrokeDescription(path,0,80)).build();
  return dispatchGesture(gesture,null,null);
 }
 public String clickCreate(String expected){
  if(busy)return "BUSY";
  return verifiedCreate(expected);
 }
 private String verifiedCreate(String expected){
  if(expected==null||!expected.matches("@[A-Za-z0-9._]{1,40}"))return "INVALID_ACCOUNT";
  AccessibilityNodeInfo root=root();if(root==null)return "TIKTOK_NOT_FOREGROUND";
  AccessibilityNodeInfo account=unique(root,"com.zhiliaoapp.musically:id/rhk");
  if(account==null||account.getText()==null||!expected.contentEquals(account.getText()))return "ACCOUNT_NOT_CONFIRMED";
  AccessibilityNodeInfo button=unique(root,"com.zhiliaoapp.musically:id/n9s");
  if(button==null||!button.isClickable()||button.getContentDescription()==null||!"Создать".contentEquals(button.getContentDescription()))return "CREATE_NOT_CONFIRMED";
  if(!click(button))return "CREATE_ACTION_REJECTED";confirmedAccount=expected;return "CREATE_ACTION_ACCEPTED";
 }
 private SharedPreferences jobRecords(){return getSharedPreferences("imports",0);}
 public String beginJobShare(String name){
  if(!SubmissionPolicy.name(name))return "INVALID_JOB";
  SharedPreferences records=jobRecords();
  if(records.getString(name+".verified",null)==null||!confirmedAccount.equals(records.getString(name+".account",null)))return "JOB_ACCOUNT_NOT_CONFIRMED";
  String result=beginShareVideo();if("SHARE_ROUTE_STARTED".equals(result))activeJob=name;return result;
 }
 private AccessibilityNodeInfo captionField(AccessibilityNodeInfo root){
  AccessibilityNodeInfo n=unique(root,"com.zhiliaoapp.musically:id/gh6");
  return n!=null&&"android.widget.EditText".contentEquals(n.getClassName()==null?"":n.getClassName())?n:null;
 }
 private boolean publicVisibilityConfirmed(AccessibilityNodeInfo root){
  int matches=0;
  for(AccessibilityNodeInfo n:root.findAccessibilityNodeInfosByViewId("com.zhiliaoapp.musically:id/xfi")){
   if(!n.refresh()||!n.isVisibleToUser()||!n.isEnabled())continue;
   AccessibilityNodeInfo row=n.getParent();
   if(row==null||!row.refresh()||!row.isVisibleToUser()||!row.isEnabled()||!row.isClickable()||!tikTok(row))continue;
   if(PublicVisibilityPolicy.row(n.getPackageName()==null?null:n.getPackageName().toString(),n.getViewIdResourceName(),n.getClassName()==null?null:n.getClassName().toString(),n.getText()==null?null:n.getText().toString(),row.getViewIdResourceName(),row.getClassName()==null?null:row.getClassName().toString(),row.getContentDescription()==null?null:row.getContentDescription().toString()))matches++;
  }
  return matches==1;
 }
 private boolean rightsGesture(){
  AccessibilityNodeInfo r=root();if(r==null)return false;
  AccessibilityNodeInfo message=unique(r,"com.zhiliaoapp.musically:id/o6w");
  AccessibilityNodeInfo box=unique(r,"com.zhiliaoapp.musically:id/eer");
  if(message==null||message.getText()==null||!"Я принимаю Подтверждение прав на использование музыки".contentEquals(message.getText())||box==null||!box.isCheckable()||box.isChecked()||!"android.widget.CheckBox".contentEquals(box.getClassName()==null?"":box.getClassName()))return false;
  Rect b=new Rect();box.getBoundsInScreen(b);Rect area=new Rect();r.getBoundsInScreen(area);
  if(b.isEmpty()||!area.contains(b))return false;
  Path path=new Path();path.moveTo(b.exactCenterX(),b.exactCenterY());
  return dispatchGesture(new GestureDescription.Builder().addStroke(new GestureDescription.StrokeDescription(path,0,80)).build(),null,null);
 }
 public String beginSubmission(final String name){
  if(busy)return "BUSY";
  if(!"EDITOR_NEXT_ACTION_ACCEPTED".equals(progress)||!SubmissionPolicy.name(name)||!name.equals(activeJob))return "JOB_ROUTE_NOT_COMPLETED";
  final SharedPreferences records=jobRecords();
  final String caption=records.getString(name+".caption",null),mode=records.getString(name+".mode",null),sha=records.getString(name+".verified",null),rights=records.getString(name+".rights",null);
  if(!SubmissionPolicy.caption(caption)||!confirmedAccount.equals(records.getString(name+".account",null))||!SubmissionPolicy.mode(mode,sha,rights))return "JOB_CONFIG_NOT_CONFIRMED";
  final String dedup="publication_attempt."+confirmedAccount+"."+sha;
  final String retryToken=records.getString(name+".retry_token",null);
  final boolean approvedRetry=retryToken!=null&&retryToken.matches("[a-f0-9]{32}")&&"15f5e6b4d389ecbb89b16c3208dc6af4ace336192ef8ea5a87a7db1964001393".equals(sha)&&"@redmaagi".equals(confirmedAccount)&&"тест".equals(caption)&&"READY".equals(records.getString("retry_authorization."+retryToken,null));
  if("PUBLISH".equals(mode)&&records.contains(dedup)&&!approvedRetry)return "PUBLICATION_PREVIOUSLY_ATTEMPTED";
  busy=true;progress="RUNNING_WAITING_CAPTION_SCREEN";
  final long deadline=SystemClock.elapsedRealtime()+45000;
  handler.post(new Runnable(){
   boolean done=false;int writes=0,rightsActions=0;boolean keyboardClosed=false;
   void finish(String result){done=true;busy=false;progress=result;}
   void again(long ms){handler.postDelayed(this,ms);}
   public void run(){
    if(done)return;if(SystemClock.elapsedRealtime()>deadline){finish("SUBMISSION_UI_TIMEOUT");return;}
    AccessibilityNodeInfo r=root();if(r==null){finish("SUBMISSION_EXTERNAL_WINDOW");return;}
    AccessibilityNodeInfo field=captionField(r),publish=unique(r,"com.zhiliaoapp.musically:id/rsn");
    if(field==null||publish==null||publish.getText()==null||!"Опубликовать".contentEquals(publish.getText())||!"android.widget.Button".contentEquals(publish.getClassName()==null?"":publish.getClassName())){again(500);return;}
    if(field.getText()==null||!caption.contentEquals(field.getText())){
     if(writes>=2){finish("CAPTION_TEXT_NOT_VERIFIED");return;}
     Bundle args=new Bundle();args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,caption);writes++;
     if(!field.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT,args)){finish("CAPTION_SET_TEXT_REJECTED");return;}
     progress="RUNNING_VERIFY_CAPTION";again(700);return;
    }
    if(!keyboardClosed){
     boolean keyboard=false;for(AccessibilityWindowInfo w:getWindows())if(w.getType()==AccessibilityWindowInfo.TYPE_INPUT_METHOD)keyboard=true;
     keyboardClosed=true;if(keyboard){performGlobalAction(GLOBAL_ACTION_BACK);again(600);return;}
    }
    if("PREPARE".equals(mode)){finish("CAPTION_PREPARED_NOT_PUBLISHED");return;}
    if(!publicVisibilityConfirmed(r)){finish("PUBLIC_VISIBILITY_NOT_CONFIRMED");return;}
    AccessibilityNodeInfo box=unique(r,"com.zhiliaoapp.musically:id/eer");
    AccessibilityNodeInfo rightsLabel=unique(r,"com.zhiliaoapp.musically:id/o6w");
    if(box==null||!box.isCheckable()||rightsLabel==null||rightsLabel.getText()==null||!"Я принимаю Подтверждение прав на использование музыки".contentEquals(rightsLabel.getText())){finish("RIGHTS_CONTROL_NOT_CONFIRMED");return;}
    if(!box.isChecked()){
     if(rightsActions++>=2){finish("RIGHTS_CHECK_NOT_CONFIRMED");return;}
     if(!click(box)&&!rightsGesture()){finish("RIGHTS_ACTION_REJECTED");return;}
     progress="RUNNING_VERIFY_RIGHTS_CHECK";again(900);return;
    }
    // Re-fetch critical controls immediately before the single publication attempt.
    r=root();if(r==null){finish("SUBMISSION_EXTERNAL_WINDOW");return;}
    field=captionField(r);publish=unique(r,"com.zhiliaoapp.musically:id/rsn");box=unique(r,"com.zhiliaoapp.musically:id/eer");
    if(!publicVisibilityConfirmed(r)){finish("PUBLIC_VISIBILITY_NOT_CONFIRMED");return;}
    if(field==null||field.getText()==null||!caption.contentEquals(field.getText())||box==null||!box.isChecked()||publish==null||publish.getText()==null||!"Опубликовать".contentEquals(publish.getText())||!publish.isClickable()){finish("FINAL_CHECK_FAILED");return;}
    String attemptKey=dedup;
    if(records.contains(dedup)){
     if(!approvedRetry||!"READY".equals(records.getString("retry_authorization."+retryToken,null))){finish("PUBLICATION_PREVIOUSLY_ATTEMPTED");return;}
     attemptKey="retry_attempt."+retryToken;
     if(!records.edit().putString("retry_authorization."+retryToken,"USED").putString(attemptKey,"ATTEMPT_RESERVED").commit()){finish("PUBLICATION_RECORD_FAILED");return;}
    }else if(!records.edit().putString(dedup,"ATTEMPT_RESERVED").commit()){finish("PUBLICATION_RECORD_FAILED");return;}
    // Single click, including this explicitly authorized test retry. Old record retained.
    boolean accepted=click(publish);
    records.edit().putString(attemptKey,accepted?"CLICK_ACCEPTED_UNVERIFIED":"CLICK_REJECTED_OR_AMBIGUOUS").commit();
    finish(accepted?"PUBLICATION_SUBMITTED_UNVERIFIED":"PUBLICATION_CLICK_REJECTED_NO_RETRY");
   }
  });return "SUBMISSION_ROUTE_STARTED";
 }
 public String beginShareVideo(){
  if(busy)return "BUSY";
  if(!"VIDEO_TAB_SELECTED".equals(progress))return "ACCOUNT_ROUTE_NOT_COMPLETED";
  busy=true;progress="RUNNING_WAITING_SHARE_SHEET";
  final long deadline=SystemClock.elapsedRealtime()+30000;
  handler.post(new Runnable(){
   int rejections=0;boolean done=false;
   void finish(String result){done=true;busy=false;progress=result;}
   public void run(){
    if(done)return;
    if(SystemClock.elapsedRealtime()>deadline){finish("SHARE_SHEET_TIMEOUT");return;}
    AccessibilityNodeInfo r=root();
    if(r==null){finish("SHARE_EXTERNAL_WINDOW");return;}
    AccessibilityNodeInfo title=unique(r,"com.zhiliaoapp.musically:id/oel");
    if(title==null||title.getText()==null||!"Поделиться в TikTok".contentEquals(title.getText())){
     handler.postDelayed(this,500);return;
    }
    AccessibilityNodeInfo target=null;
    for(AccessibilityNodeInfo n:r.findAccessibilityNodeInfosByViewId("com.zhiliaoapp.musically:id/p48")){
     if(!n.refresh()||!n.isVisibleToUser()||!n.isEnabled()||!n.isClickable())continue;
     if(!TIKTOK.contentEquals(n.getPackageName()==null?"":n.getPackageName()))continue;
     if(n.getText()==null||!"Видео".contentEquals(n.getText()))continue;
     if(n.getClassName()==null||!"android.widget.Button".contentEquals(n.getClassName()))continue;
     if(target!=null){finish("SHARE_VIDEO_AMBIGUOUS");return;}target=n;
    }
    if(target==null){handler.postDelayed(this,500);return;}
    if(!click(target)){
     if(++rejections>=5){finish("SHARE_VIDEO_CLICK_REJECTED");return;}
     progress="RUNNING_RECHECK_SHARE_BUTTON";handler.postDelayed(this,600);return;
    }
    // Accepted click is NOT claimed to prove editor readiness. Mac captures next UI.
    finish("SHARE_VIDEO_ACTION_ACCEPTED");
   }
  });
  return "SHARE_ROUTE_STARTED";
 }
 public String beginEditorNext(){
  if(busy)return "BUSY";
  if(!"SHARE_VIDEO_ACTION_ACCEPTED".equals(progress))return "SHARE_ROUTE_NOT_COMPLETED";
  busy=true;progress="RUNNING_WAITING_EDITOR";
  final long deadline=SystemClock.elapsedRealtime()+30000;
  handler.post(new Runnable(){
   boolean done=false;int rejects=0;
   void finish(String result){done=true;busy=false;progress=result;}
   public void run(){
    if(done)return;
    if(SystemClock.elapsedRealtime()>deadline){finish("EDITOR_NEXT_TIMEOUT");return;}
    AccessibilityNodeInfo r=root();if(r==null){finish("EDITOR_EXTERNAL_WINDOW");return;}
    AccessibilityNodeInfo music=unique(r,"com.zhiliaoapp.musically:id/dip");
    AccessibilityNodeInfo story=unique(r,"com.zhiliaoapp.musically:id/zdn");
    AccessibilityNodeInfo label=unique(r,"com.zhiliaoapp.musically:id/om2");
    if(music==null||music.getContentDescription()==null||!"Музыка".contentEquals(music.getContentDescription())||
      story==null||story.getText()==null||!"Ваша история".contentEquals(story.getText())||
      label==null||label.getText()==null||!"Далее".contentEquals(label.getText())){
     handler.postDelayed(this,500);return;
    }
    AccessibilityNodeInfo button=label.getParent();
    if(button==null||!button.refresh()||!"com.zhiliaoapp.musically:id/olz".equals(button.getViewIdResourceName())||
      !TIKTOK.contentEquals(button.getPackageName()==null?"":button.getPackageName())||
      button.getClassName()==null||!"android.widget.LinearLayout".contentEquals(button.getClassName())){
     finish("EDITOR_NEXT_PARENT_NOT_CONFIRMED");return;
    }
    if(!click(button)){
     if(++rejects>=5){finish("EDITOR_NEXT_CLICK_REJECTED");return;}
     handler.postDelayed(this,600);return;
    }
    // Next only. No caption entry, visibility changes, story or publication clicks.
    finish("EDITOR_NEXT_ACTION_ACCEPTED");
   }
  });return "EDITOR_NEXT_ROUTE_STARTED";
 }
 public String routeProgress(){return progress;}
 public String beginOpenVideos(final String expected){
  if(busy)return "BUSY";
  if(expected==null||!expected.matches("@[A-Za-z0-9._]{1,40}"))return "INVALID_ACCOUNT";
  openVideos(expected,new Completion(){public void done(String result){progress=result;}});
  return "ROUTE_STARTED";
 }
 public void openVideos(final String expected,final Completion completion){
  if(busy){completion.done("BUSY");return;}
  if(expected==null||!expected.matches("@[A-Za-z0-9._]{1,40}")){completion.done("INVALID_ACCOUNT");return;}
  busy=true;progress="RUNNING_NAVIGATE";
  final long started=SystemClock.elapsedRealtime();
  final long deadline=started+120000;
  Runnable sequence=new Runnable(){
   String stage="NAVIGATE";String lastPackage="UNKNOWN";boolean done=false;int navigationActions=0;int deniedReminders=0;int denyFailures=0;int gestureAttempts=0;int galleryClicks=0;int galleryRejections=0;int draftActions=0;int draftFailures=0;long galleryLastAttempt=0;boolean requestedProfile=false;boolean seenTikTok=false;long unavailableSince=0;
   void updateDiagnostics(){diagnostics="denial_actions="+deniedReminders+";denial_failures="+denyFailures+";denial_gestures="+gestureAttempts+";gallery_clicks="+galleryClicks+";gallery_rejections="+galleryRejections+";last_pkg="+lastPackage+";draft_actions="+draftActions+";draft_failures="+draftFailures+";stage="+stage;}
   void finish(String result){updateDiagnostics();if(done)return;done=true;busy=false;progress=result;completion.done(result);}
   void again(long delay){handler.postDelayed(this,delay);}
   @Override public void run(){
    if(done)return;
    if(SystemClock.elapsedRealtime()>deadline){finish("UI_TIMEOUT_"+stage);return;}
    AccessibilityNodeInfo raw=getRootInActiveWindow();
    String pkg=raw==null||raw.getPackageName()==null?"":raw.getPackageName().toString();lastPackage=pkg;
    AccessibilityNodeInfo root=root();
    if(root==null){
     long now=SystemClock.elapsedRealtime();
     if(unavailableSince==0)unavailableSince=now;
     boolean coldStart="NAVIGATE".equals(stage)&&!seenTikTok&&now-started<45000;
     boolean loadingWindow=raw==null||"com.miui.home".equals(pkg)||"android".equals(pkg)||"com.android.systemui".equals(pkg);
     if(loadingWindow&&(coldStart||now-unavailableSince<20000)){
      progress="RUNNING_WAITING_TIKTOK";again(500);return;
     }
     finish("PERMISSION_OR_EXTERNAL_WINDOW");return;
    }
    seenTikTok=true;unavailableSince=0;updateDiagnostics();progress="RUNNING_"+stage;
    AccessibilityNodeInfo draft=resumeDraftRoot();
    if(draft!=null){
     progress="RUNNING_SAVE_PREVIOUS_DRAFT";
     if(draftActions>=3){finish("RESUME_DRAFT_REPEATED");return;}
     AccessibilityNodeInfo target=resumeSaveTarget(draft);
     if(target==null||!click(target)){
      if(++draftFailures>=5){finish("RESUME_DRAFT_SAVE_NOT_CONFIRMED");return;}
      again(600);return;
     }
     draftActions++;draftFailures=0;again(1200);return;
    }
    if(isCameraMicReminder(root)){
     if(deniedReminders>=6){finish("CAMERA_MIC_REMINDER_REPEATED");return;}
     progress="RUNNING_DENY_CAMERA_MIC";
     if(click(denyTarget(root))){deniedReminders++;denyFailures=0;again(900);return;}
     denyFailures++;
     if(denyFailures>=2&&gestureAttempts<2){
      gestureAttempts++;
      if(denyGesture()){deniedReminders++;again(1000);return;}
     }
     if(denyFailures>=6){finish("CAMERA_MIC_DENY_NOT_CONFIRMED");return;}
     again(450);return;
    }
    denyFailures=0;
    AccessibilityNodeInfo galleryHeader=unique(root,"com.zhiliaoapp.musically:id/zq_");
    if("NAVIGATE".equals(stage)){
     if(navigationActions>8){finish("NAVIGATION_LIMIT");return;}
     // Return from recognized gallery/camera only; never dismiss arbitrary security screens.
     if(galleryHeader!=null){navigationActions++;if(!performGlobalAction(GLOBAL_ACTION_BACK)){finish("BACK_REJECTED");return;}again(800);return;}
     AccessibilityNodeInfo upload=unique(root,"com.zhiliaoapp.musically:id/upload_hot_area");
     if(upload!=null){
      AccessibilityNodeInfo close=unique(root,"com.zhiliaoapp.musically:id/pnw");
      if(close==null||close.getContentDescription()==null||!"Закрыть".contentEquals(close.getContentDescription())||!click(close)){finish("CAMERA_CLOSE_NOT_CONFIRMED");return;}
      navigationActions++;again(800);return;
     }
     AccessibilityNodeInfo account=unique(root,"com.zhiliaoapp.musically:id/rhk");
     if(account!=null){
      if(account.getText()==null||!expected.contentEquals(account.getText())){finish("ACCOUNT_MISMATCH");return;}
      String result=verifiedCreate(expected);
      if("CREATE_NOT_CONFIRMED".equals(result)){progress="RUNNING_WAITING_CREATE_BUTTON";again(700);return;}
      if(!"CREATE_ACTION_ACCEPTED".equals(result)){finish(result);return;}
      stage="OPEN_GALLERY";again(1000);return;
     }
     AccessibilityNodeInfo profile=unique(root,"com.zhiliaoapp.musically:id/n9x");
     if(profile!=null&&profile.getContentDescription()!=null&&"Профиль".contentEquals(profile.getContentDescription())){
      if(!requestedProfile){
       if(!click(profile)){finish("PROFILE_ACTION_REJECTED");return;}
       requestedProfile=true;navigationActions++;
      }
      progress="RUNNING_WAITING_PROFILE";again(800);return;
     }
     again(500);return;
    }
    if("OPEN_GALLERY".equals(stage)){
     if(galleryHeader!=null){stage="VIDEO_TAB";again(300);return;}
     AccessibilityNodeInfo upload=unique(root,"com.zhiliaoapp.musically:id/upload_hot_area");
     if(upload==null){again(500);return;}
     long now=SystemClock.elapsedRealtime();
     if(now-galleryLastAttempt<1500){again(400);return;}
     if(galleryClicks>=6||galleryRejections>=6){finish("GALLERY_RETRY_LIMIT");return;}
     // A modal can appear after lookup. A rejected click is not terminal: next pass
     // re-fetches the topmost app window and processes the known dialog first.
     galleryLastAttempt=now;
     if(!click(upload)){galleryRejections++;progress="RUNNING_RECHECK_GALLERY_WINDOW";again(500);return;}
     galleryClicks++;again(900);return;
    }
    if("VIDEO_TAB".equals(stage)){
     if(galleryHeader==null){again(500);return;}
     AccessibilityNodeInfo target=null;
     for(AccessibilityNodeInfo n:root.findAccessibilityNodeInfosByText("Видео")){
      if(n.isVisibleToUser()&&n.isEnabled()&&n.getContentDescription()!=null&&"Видео".contentEquals(n.getContentDescription())){
       if(target!=null){finish("VIDEO_TAB_AMBIGUOUS");return;}target=n;
      }
     }
     if(target==null){finish("VIDEO_TAB_NOT_FOUND");return;}
     if(target.isSelected()){finish("VIDEO_TAB_SELECTED");return;}
     if(!click(target)){progress="RUNNING_RECHECK_VIDEO_TAB";again(500);return;}
     again(700);
    }
   }
  };
  handler.post(sequence);
 }
}
