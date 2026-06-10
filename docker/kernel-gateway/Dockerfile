FROM python:3.14

WORKDIR /workspace

RUN pip install --no-cache-dir \
    ipykernel \
    jupyter_kernel_gateway

EXPOSE 8888

CMD ["jupyter", "kernelgateway", "--KernelGatewayApp.ip=0.0.0.0", "--KernelGatewayApp.port=8888"]
