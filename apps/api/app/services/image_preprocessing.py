"""Preprocesamiento de imágenes escaneadas para mejorar OCR (Fase 2).

Pipeline ligero pensado para documentos judiciales escaneados en blanco y negro:
1. Escala de grises.
2. Corrección de inclinación (deskew) vía `deskew`.
3. Reducción de ruido (filtro mediana).
4. Binarización adaptativa (Otsu o local) para resaltar texto.
"""
from __future__ import annotations

import logging

import cv2
import numpy as np
from PIL import Image
from deskew import determine_skew

log = logging.getLogger(__name__)


def _pil_to_cv2(image: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


def _cv2_to_pil(array: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(array, cv2.COLOR_BGR2RGB))


def grayscale(image: Image.Image) -> Image.Image:
    return image.convert("L").convert("RGB")


def deskew(image: Image.Image) -> Image.Image:
    """Corrige inclinación usando la librería `deskew`."""
    gray = np.array(image.convert("L"))
    angle = determine_skew(gray)
    if angle is None or abs(angle) < 0.25:
        return image
    (h, w) = gray.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return Image.fromarray(rotated).convert("RGB")


def denoise(image: Image.Image) -> Image.Image:
    """Filtro mediana para eliminar sal y pimienta sin borrar bordes finos."""
    gray = np.array(image.convert("L"))
    denoised = cv2.medianBlur(gray, 3)
    return Image.fromarray(denoised).convert("RGB")


def binarize(image: Image.Image) -> Image.Image:
    """Binarización adaptativa local; funciona bien con fondos desiguales."""
    gray = np.array(image.convert("L"))
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
    )
    return Image.fromarray(binary).convert("RGB")


def clahe(image: Image.Image, clip: float = 2.0, grid: int = 8) -> Image.Image:
    """Mejora contraste local con CLAHE; útil para texto apagado."""
    gray = np.array(image.convert("L"))
    clahe_obj = cv2.createCLAHE(clipLimit=clip, tileGridSize=(grid, grid))
    enhanced = clahe_obj.apply(gray)
    return Image.fromarray(enhanced).convert("RGB")


def denoise_bilateral(image: Image.Image, d: int = 9, sigma: int = 75) -> Image.Image:
    """Filtro bilateral: reduce ruido preservando bordes de texto."""
    gray = np.array(image.convert("L"))
    denoised = cv2.bilateralFilter(gray, d=d, sigmaColor=sigma, sigmaSpace=sigma)
    return Image.fromarray(denoised).convert("RGB")


def remove_speckles(image: Image.Image, kernel_size: int = 2) -> Image.Image:
    """Elimina manchas/puntos pequeños sin afectar el texto tipográfico."""
    gray = np.array(image.convert("L"))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    cleaned = cv2.morphologyEx(gray, cv2.MORPH_OPEN, kernel, iterations=1)
    return Image.fromarray(cleaned).convert("RGB")


def unsharp_mask(image: Image.Image, amount: float = 1.5, radius: float = 1.0) -> Image.Image:
    """Aumenta nitidez de bordes."""
    gray = np.array(image.convert("L"), dtype=np.float32) / 255.0
    blurred = cv2.GaussianBlur(gray, (0, 0), radius)
    sharpened = gray + amount * (gray - blurred)
    sharpened = np.clip(sharpened * 255, 0, 255).astype(np.uint8)
    return Image.fromarray(sharpened).convert("RGB")


def preprocess(image: Image.Image) -> Image.Image:
    """Aplica el pipeline completo."""
    image = grayscale(image)
    image = deskew(image)
    image = denoise(image)
    image = binarize(image)
    return image


def preprocess_aggressive(image: Image.Image) -> Image.Image:
    """Pipeline más agresivo para páginas difíciles (sellos, manchas, bajo contraste).

    No activar por defecto; usar bajo demanda cuando el OCR base dé confianza < 0.85.
    """
    image = grayscale(image)
    image = deskew(image)
    image = clahe(image)
    image = denoise_bilateral(image)
    image = remove_speckles(image)
    image = unsharp_mask(image)
    image = binarize(image)
    return image


def preprocess_super_aggressive(image: Image.Image) -> Image.Image:
    """Pipeline aún más fuerte para páginas que no responden al agresivo estándar.

    Usa CLAHE más intenso, eliminación de manchas mayor y nitidez extra.
    """
    image = grayscale(image)
    image = deskew(image)
    image = clahe(image, clip=4.0, grid=16)
    image = denoise_bilateral(image, d=11, sigma=100)
    image = remove_speckles(image, kernel_size=3)
    image = unsharp_mask(image, amount=2.0, radius=1.5)
    image = binarize(image)
    return image
