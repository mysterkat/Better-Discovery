interface FilePickerProps {
  label: string;
  value: string;
  onChange: (path: string) => void;
  placeholder?: string;
  hint?: string;
}

export default function FilePicker({
  label,
  value,
  onChange,
  placeholder,
  hint,
}: FilePickerProps) {
  return (
    <div className="field">
      <label className="field-label">{label}</label>
      <input
        className="field-input"
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder ?? "Enter file path…"}
        spellCheck={false}
      />
      {hint && <span className="field-hint">{hint}</span>}
    </div>
  );
}
