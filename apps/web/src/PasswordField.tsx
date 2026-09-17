import { useId, useState } from "react";
import { Eye, EyeOff, RefreshCw } from "lucide-react";

export default function PasswordField({ label, value, onChange, generate = false, readOnly = false, current = false }: {
  label: string; value: string; onChange?: (value: string) => void;
  generate?: boolean; readOnly?: boolean; current?: boolean;
}) {
  const id = useId();
  const [visible, setVisible] = useState(false);
  function generatePassword() {
    const values = crypto.getRandomValues(new Uint8Array(20));
    const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#%";
    onChange?.("Aa9-" + [...values].map((value) => alphabet[value % alphabet.length]).join(""));
    setVisible(true);
  }
  return <div className="password-field">
    <label htmlFor={id}>{label}</label>
    <div className="password-control">
      <input id={id} type={visible ? "text" : "password"} autoComplete={current ? "current-password" : "new-password"}
        value={value} required minLength={current ? 1 : 12} maxLength={128} readOnly={readOnly}
        onChange={(event) => onChange?.(event.target.value)} />
      {generate && <button type="button" className="icon-button" aria-label="生成初始密码" title="生成初始密码" onClick={generatePassword}><RefreshCw size={17} /></button>}
      <button type="button" className="icon-button" aria-label={visible ? `隐藏${label}` : `显示${label}`} title={visible ? "隐藏密码" : "显示密码"} onClick={() => setVisible(!visible)}>{visible ? <EyeOff size={17} /> : <Eye size={17} />}</button>
    </div>
  </div>;
}
