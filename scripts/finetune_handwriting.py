#!/usr/bin/env python3
"""Fine-tuning de un OCR de línea (TrOCR) con el corpus de caligrafía generado por
`scripts/build_handwriting_dataset.py`.

Entrena el modelo para escribir **español** (no inglés) con la letra real del expediente.
Al terminar, el modelo queda en `--output` y se usa en el pipeline con:

    OCR_PROVIDER=handwriting
    OCR_HANDWRITING_MODEL=<ruta al output>

Bucle de entrenamiento explícito (sin `Trainer`) para no depender de la API cambiante de
`transformers`. En CPU sirve para validar con `--smoke`; el entrenamiento real conviene en GPU.

Uso:
    .venv\\Scripts\\python scripts\\finetune_handwriting.py --dataset var/handwriting_dataset --output var/handwriting_model
    .venv\\Scripts\\python scripts\\finetune_handwriting.py --dataset var/handwriting_dataset --smoke
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torch.utils.data import DataLoader, Dataset

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.services.handwriting_ocr import load_trocr_processor  # noqa: E402
from app.services.ocr_metrics import cer, wer  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("finetune_handwriting")
MAX_LABEL_LEN = 48


class LineDataset(Dataset):
    def __init__(self, records: list[dict], root: Path, processor, augment: bool = False):
        self.records = records
        self.root = root
        self.processor = processor
        self.augment = augment

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int) -> dict:
        rec = self.records[idx]
        image = Image.open(self.root / rec["image"]).convert("RGB")
        pixel = self.processor(images=image, return_tensors="pt").pixel_values.squeeze(0)
        ids = self.processor.tokenizer(
            rec["text"], padding="max_length", max_length=MAX_LABEL_LEN, truncation=True
        ).input_ids
        pad = self.processor.tokenizer.pad_token_id
        labels = [t if t != pad else -100 for t in ids]
        return {"pixel_values": pixel, "labels": torch.tensor(labels, dtype=torch.long)}


def _load_records(dataset: Path) -> list[dict]:
    path = dataset / "metadata.jsonl"
    if not path.exists():
        raise SystemExit(f"[finetune] falta {path}. Ejecuta build_handwriting_dataset.py primero.")
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


@torch.no_grad()
def evaluate(model, processor, loader) -> dict[str, float]:
    model.eval()
    cers, wers = [], []
    for batch in loader:
        pixel = batch["pixel_values"]
        labels = batch["labels"].clone()
        ids = model.generate(pixel, max_new_tokens=MAX_LABEL_LEN)
        preds = processor.batch_decode(ids, skip_special_tokens=True)
        labels[labels == -100] = processor.tokenizer.pad_token_id
        refs = processor.batch_decode(labels, skip_special_tokens=True)
        for ref, hyp in zip(refs, preds, strict=True):
            cers.append(cer(ref, hyp))
            wers.append(wer(ref, hyp))
    model.train()
    return {"cer": float(np.mean(cers)) if cers else 0.0,
            "wer": float(np.mean(wers)) if wers else 0.0}


def train(model, processor, train_loader, val_loader, *, epochs: int, lr: float) -> None:
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr)
    model.train()
    for epoch in range(1, epochs + 1):
        running = 0.0
        for step, batch in enumerate(train_loader, start=1):
            out = model(pixel_values=batch["pixel_values"], labels=batch["labels"])
            loss = out.loss
            loss.backward()
            optimizer.step()
            optimizer.zero_grad()
            running += float(loss)
            log.info("epoch %d step %d loss=%.4f", epoch, step, float(loss))
        metrics = evaluate(model, processor, val_loader) if val_loader else {}
        log.info("epoch %d: loss_media=%.4f %s", epoch, running / max(1, step), metrics)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="var/handwriting_dataset")
    ap.add_argument("--base-model", default="microsoft/trocr-base-handwritten")
    ap.add_argument("--output", default="var/handwriting_model")
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--lr", type=float, default=5e-5)
    ap.add_argument("--val-split", type=float, default=0.15)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--smoke", action="store_true", help="1 época, 4 ejemplos, batch 1 (validación rápida)")
    args = ap.parse_args()

    if args.smoke:
        args.epochs, args.batch_size, args.val_split = 1, 1, 0.25

    dataset = Path(args.dataset)
    records = _load_records(dataset)
    if not records:
        raise SystemExit("[finetune] el dataset está vacío: corrige más páginas y regenera el corpus.")
    random.Random(args.seed).shuffle(records)
    if args.smoke:
        records = records[:4]
    if len(records) < 20:
        log.warning("solo %d ejemplos: insuficiente para un modelo útil (se recomiendan >=500 líneas)", len(records))

    val_n = max(1, int(len(records) * args.val_split)) if len(records) > 1 else 0
    val_records, train_records = records[:val_n], records[val_n:] or records

    log.info("base=%s | train=%d val=%d", args.base_model, len(train_records), len(val_records))
    from transformers import VisionEncoderDecoderModel

    processor = load_trocr_processor(args.base_model)
    model = VisionEncoderDecoderModel.from_pretrained(args.base_model)
    # Receta estándar de fine-tuning de TrOCR: tokens de arranque/pad/fin del decodificador.
    model.config.decoder_start_token_id = processor.tokenizer.cls_token_id
    model.config.pad_token_id = processor.tokenizer.pad_token_id
    model.config.eos_token_id = processor.tokenizer.sep_token_id
    model.config.vocab_size = model.config.decoder.vocab_size

    train_loader = DataLoader(LineDataset(train_records, dataset, processor, augment=True),
                              batch_size=args.batch_size, shuffle=True)
    val_loader = DataLoader(LineDataset(val_records, dataset, processor),
                            batch_size=args.batch_size) if val_records else None

    train(model, processor, train_loader, val_loader, epochs=args.epochs, lr=args.lr)

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(out)
    processor.save_pretrained(out)
    (out / "training_meta.json").write_text(json.dumps({
        "base_model": args.base_model, "train": len(train_records), "val": len(val_records),
        "epochs": args.epochs, "lr": args.lr, "smoke": args.smoke,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("modelo guardado en %s", out)


if __name__ == "__main__":
    main()
