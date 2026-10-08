package com.faxclip.access;
public final class VerificationFreshnessPolicy {
 private VerificationFreshnessPolicy(){}
 public static boolean fresh(boolean textChanged,long baseline,long current){return textChanged||(baseline>0&&current>baseline);}
}
