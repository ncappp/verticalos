package com.faxclip.access;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
public final class ControlReceiver extends BroadcastReceiver {
 @Override public void onReceive(Context context,Intent intent){
  if(!"com.faxclip.access.CONTROL".equals(intent.getAction())){setResultCode(0);return;}
  FaxClipAccessibility service=FaxClipAccessibility.instance;
  if(service==null){setResultCode(0);setResultData("SERVICE_NOT_CONNECTED");return;}
  String command=intent.getStringExtra("cmd");
  if("verification_capabilities".equals(command)){setResultData("FRESH_CLIP_TIMESTAMP_V1");return;}
  if("status".equals(command)){setResultCode(1);setResultData("SERVICE_CONNECTED_V14");return;}
  if("verification_job".equals(command)){setResultData(service.verificationJob(intent.getStringExtra("job_name")));return;}
  if("verification_candidate".equals(command)){setResultData(service.verificationCandidate(intent.getIntExtra("index",-1)));return;}
  if("verification_inspect".equals(command)){setResultData(service.verificationInspect(false));return;}
  if("verification_reopened".equals(command)){setResultData(service.verificationInspect(true));return;}
  if("verification_share".equals(command)){setResultData(service.verificationShare());return;}
  if("verification_copy_link".equals(command)){setResultData(service.verificationCopyLink());return;}
  if("verification_link_result".equals(command)){
   android.content.SharedPreferences prefs=context.getSharedPreferences("verification",0);
   String token=intent.getStringExtra("token");
   if(token==null||!token.equals(prefs.getString("token",null))){setResultData("VERIFICATION_TOKEN_MISMATCH");return;}
   setResultData(prefs.getString("result","NO_LINK_RESULT")+";url="+prefs.getString("url",""));return;
  }
  if("tiktok_create".equals(command)){
   String result=service.clickCreate(intent.getStringExtra("expected_account"));setResultCode("CREATE_ACTION_ACCEPTED".equals(result)?1:0);setResultData(result);return;
  }
  if("tiktok_open_videos_start".equals(command)){
   String result=service.beginOpenVideos(intent.getStringExtra("expected_account"));
   setResultCode("ROUTE_STARTED".equals(result)?1:0);setResultData(result);return;
  }
  if("tiktok_share_video_start".equals(command)){
   String result=service.beginJobShare(intent.getStringExtra("job_name"));setResultCode("SHARE_ROUTE_STARTED".equals(result)?1:0);setResultData(result);return;
  }
  if("tiktok_editor_next_start".equals(command)){
   String result=service.beginEditorNext();setResultCode("EDITOR_NEXT_ROUTE_STARTED".equals(result)?1:0);setResultData(result);return;
  }
  if("tiktok_submission_start".equals(command)){
   String result=service.beginSubmission(intent.getStringExtra("job_name"));setResultCode("SUBMISSION_ROUTE_STARTED".equals(result)?1:0);setResultData(result);return;
  }
  if("tiktok_route_state".equals(command)){
   setResultCode(1);setResultData(service.routeProgress());return;
  }
  if("window_state".equals(command)){setResultCode(1);setResultData(service.windowState());return;}
  if("tiktok_route_diagnostics".equals(command)){setResultCode(1);setResultData(service.routeDiagnostics());return;}
  setResultCode(0);setResultData("UNSUPPORTED_COMMAND");
 }
}
